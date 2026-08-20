"""
pywebview 桌面入口

启动流程：
1. 找一个可用端口
2. 后台线程启动 uvicorn (FastAPI)
3. 轮询 /api/health 等待后端就绪
4. pywebview 打开原生窗口 (1600x900)
5. 窗口关闭时终止全部

注意：处理 Windows 中文路径下相对导入失败的问题：
- 强制切换 cwd 到项目根目录（脚本所在目录）
- 把项目根目录加入 sys.path
- 任何路径相关操作都用绝对路径
"""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

# === 关键：Windows 下 stdout/stderr 默认按 GBK 编码；本项目大量 print 含中文/emoji
# （如 ⚠️），一旦重定向到文件（run_desktop.bat 的 run.log）就会抛 UnicodeEncodeError，
# 进而让配置加载、请求处理等链路整体崩溃、所有接口返回 500。
# 重配为 UTF-8 + errors='replace'，确保任何 print 都不会中断进程。
import io as _io
try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# === 关键：解决中文路径下 import backend.app 失败 ===
# NOVEL_ROOT 允许从“本地副本”启动进程（WebView2 要求进程路径在本地盘），
# 同时把所有数据/日志仍指向共享盘上的真实项目根目录。
PROJECT_ROOT = Path(os.environ.get("NOVEL_ROOT") or str(Path(__file__).resolve().parent)).resolve()
os.chdir(str(PROJECT_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("desktop")


def _install_crash_diagnostics():
    """
    把未捕获异常 / 线程异常 / 原生 stderr 输出全部落盘，便于排查「闪退」。
    默认情况下桌面端 stderr 没有文件落盘，进程被 os._exit 或渲染进程崩溃带走时
    任何 traceback / pywebview(WebView2) 原生报错都会丢失，难以定位。
    """
    import sys
    import threading

    crash_path = PROJECT_ROOT / "crash.log"
    crash_logger = logging.getLogger("crash")
    crash_logger.setLevel(logging.ERROR)
    crash_logger.propagate = False
    if not any(
        getattr(h, "baseFilename", "") == str(crash_path)
        for h in crash_logger.handlers
    ):
        # H14 修复：原用裸 FileHandler 不限制大小，crash.log 会无限增长（实测已达 45MB）。
        # 改用 RotatingFileHandler，与 analyzer.log 同策略（10MB × 3），避免长期运行撑爆磁盘。
        fh = RotatingFileHandler(
            str(crash_path), encoding="utf-8",
            maxBytes=10 * 1024 * 1024, backupCount=3,
        )
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        crash_logger.addHandler(fh)

    def log_exc(msg, exc_info):
        crash_logger.error(msg, exc_info=exc_info)
        # 同时保留解释器默认行为（打印到原 stderr）
        sys.__excepthook__(*exc_info) if exc_info else None

    def _excepthook(t, v, tb):
        log_exc("Uncaught exception (main thread)", (t, v, tb))

    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        log_exc(
            "Uncaught exception in thread",
            (args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_excepthook

    # 重定向 stderr 到「原 stderr + crash.log」，捕获 pywebview / WebView2 原生报错
    crash_fh = open(crash_path, "a", encoding="utf-8", buffering=1)

    class _StderrTee:
        def __init__(self, original, fileobj):
            self._orig = original
            self._f = fileobj

        def write(self, s):
            try:
                self._f.write(s)
                self._f.flush()
            except Exception:
                pass
            try:
                self._orig.write(s)
            except Exception:
                pass

        def flush(self):
            try:
                self._f.flush()
            except Exception:
                pass
            try:
                self._orig.flush()
            except Exception:
                pass

        def __getattr__(self, name):
            return getattr(self._orig, name)

    sys.stderr = _StderrTee(sys.stderr, crash_fh)
    logger.info(f"崩溃诊断已启用，原生报错将写入: {crash_path}")


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


LOCK_FILE = PROJECT_ROOT / "app.lock"


def _read_lock() -> dict:
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _instance_alive(lock: dict) -> bool:
    """以锁中端口做健康检查判定活实例（比 PID 存活检测跨平台可靠）"""
    port = lock.get("port")
    if not port:
        return False
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
        return True
    except Exception:
        return False


def _tasks_running(port: int) -> bool:
    """分析或总结任一在运行即返回 True；查询失败（后端已挂）视为未运行，放行关闭"""
    for url in (f"http://127.0.0.1:{port}/api/analysis/status",
                f"http://127.0.0.1:{port}/api/summary/status"):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("running"):
                return True
        except Exception:
            continue
    return False


def _confirm_exit(window) -> bool:
    """弹原生确认框；返回 True=允许退出"""
    return bool(window.create_confirmation_dialog(
        "确认退出",
        "分析或总结任务正在进行中。\n已完成的进度已落盘，下次启动可断点续跑。\n确定要退出吗？"))


def _start_server(port: int):
    import uvicorn
    # 压低访问日志：仅输出应用本身的 logger（LLM 调用、token、流程等），隐藏高频 GET 请求
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            # uvicorn 本身的访问日志设为 WARNING，避免 GET 请求刷屏
            "uvicorn.access": {"handlers": ["default"], "level": "WARNING", "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["default"], "level": "INFO"},
    }
    uvicorn.run(
        "backend.app:app",
        host="127.0.0.1",
        port=port,
        log_config=log_config,
        app_dir=str(PROJECT_ROOT),
    )


def _wait_for_server(port: int, timeout: int = 30) -> bool:
    for _ in range(timeout * 2):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


class Api:
    """暴露给前端的 pywebview JS API（window.pywebview.api）"""

    def __init__(self, port: int):
        self._port = port

    def pick_files(self):
        """
        打开系统文件对话框选择 txt 小说，返回文件路径列表。
        （Windows WebView2 的 <input type=file> 不给 File 对象注入 path 属性，
        必须走后端 create_file_dialog 才能拿到真实本地路径。）
        """
        import webview

        window = webview.windows[0]
        return window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("TXT 文件 (*.txt)",),
        )

    def minimize(self):
        """最小化窗口（标题栏按钮）"""
        import webview
        try:
            webview.windows[0].minimize()
        except Exception:
            pass

    def toggle_maximize(self):
        """最大化 / 还原窗口（标题栏按钮）"""
        import webview
        try:
            win = webview.windows[0]
            if getattr(win, "maximized", False):
                win.restore()
            else:
                win.maximize()
        except Exception:
            pass

    def close(self):
        """关闭窗口（标题栏按钮）：任务运行中先弹确认"""
        import webview
        try:
            win = webview.windows[0]
            if _tasks_running(self._port) and not _confirm_exit(win):
                return
            win.destroy()
        except Exception:
            pass

    def move(self, dx, dy):
        """按增量移动窗口（标题栏拖拽）"""
        import webview
        try:
            win = webview.windows[0]
            win.move(int(win.x + dx), int(win.y + dy))
        except Exception:
            pass


def main():
    _install_crash_diagnostics()

    port = _find_free_port()
    logger.info(f"启动后端服务: http://127.0.0.1:{port}")
    logger.info(f"项目根目录: {PROJECT_ROOT}")

    # 单实例保护（2026-08-17 优化）：
    # 原实现在服务器就绪后才写锁，竞态窗口长达数秒——两个实例同时启动、在“都还没写锁”的
    # 窗口内会双双通过 exists() 检查并各自起服务。
    # 现改为：抢到端口后立即用 os.open(O_CREAT|O_EXCL) 原子创建锁文件，窗口缩到微秒级；
    # 抢到锁即写入端口，让随后启动的实例立刻读到锁并做健康检查：活实例→提示退出，
    # 端口未就绪/进程已崩→视为上次残留，删锁后重试抢锁（最多 3 次）。
    for _attempt in range(3):
        try:
            _lock_fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            old_lock = _read_lock()
            if _instance_alive(old_lock):
                logger.warning("检测到已有实例在运行，本实例退出")
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0, "小说智能分析器已在运行中。\n如确认无实例运行，请删除项目根目录的 app.lock 后重试。",
                        "已在运行", 0x40)
                except Exception:
                    print("小说智能分析器已在运行中")
                sys.exit(0)
            try:
                LOCK_FILE.unlink()
            except OSError:
                pass
    else:
        logger.error("无法获取单实例锁（反复抢锁失败），退出")
        sys.exit(1)
    try:
        os.write(_lock_fd, json.dumps(
            {"pid": os.getpid(), "port": port, "started_at": time.time()},
            ensure_ascii=False).encode("utf-8"))
        os.fsync(_lock_fd)
    except OSError as e:
        logger.warning(f"写入单实例锁失败（不影响启动）: {e}")
    finally:
        try:
            os.close(_lock_fd)
        except OSError:
            pass

    # 后台线程启动后端
    t = threading.Thread(target=_start_server, args=(port,), daemon=True)
    t.start()

    # 等后端就绪
    if not _wait_for_server(port):
        logger.error("后端启动超时")
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
        sys.exit(1)
    logger.info("后端已就绪")

    # 打开原生窗口
    try:
        import webview
    except ImportError:
        logger.error("pywebview 未安装，请运行: pip install pywebview")
        sys.exit(1)

    window = webview.create_window(
        "小说智能分析器",
        f"http://127.0.0.1:{port}/?v={int(time.time())}",
        width=1600,
        height=900,
        min_size=(960, 700),
        text_select=True,
        js_api=Api(port),
    )

    # 记录窗口关闭 / 前端加载异常，便于区分「用户主动关」还是「渲染进程崩溃」
    def _on_closed():
        logger.warning("窗口已关闭（可能是用户主动关闭，也可能是渲染进程崩溃）")

    def _on_closing():
        """OS 关窗（Alt+F4/任务栏关闭）拦截：任务运行中需确认，返回 False 否决关闭"""
        try:
            if _tasks_running(port):
                if not _confirm_exit(window):
                    return False
        except Exception as e:
            logger.error(f"关闭确认检查异常，放行关闭: {e}")
        return True

    def _on_load_exc(e):
        logger.error(f"前端页面加载异常: {e}")

    try:
        window.events.closing += _on_closing
        window.events.closed += _on_closed
        window.events.load_exception += _on_load_exc
    except Exception as e:
        logger.debug(f"注册窗口事件回调失败（pywebview 版本旧？）: {e}")

    webview.start()
    # 窗口关闭后清理锁并退出
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass
    # H2b 修复：原直接 os._exit(0) 跳过所有 atexit、不 flush 缓冲区，会丢失最后几行日志、
    # 且硬杀仍在跑的 uvicorn 守护线程（在途写文件可能截断）。退出前先 logging.shutdown()
    # 刷盘所有 handler（含 analyzer.log 与 crash.log 的 RotatingFileHandler），再结束进程。
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()