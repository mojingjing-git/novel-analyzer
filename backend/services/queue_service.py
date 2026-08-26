"""
队列服务
- QueueItem / QueueManager：自旧项目 core/queue_manager.py 移植（check_api 改 async）
- AnalysisService：队列运行编排层（替代旧 main_window 的队列驱动逻辑）
  持有当前 AnalysisPipeline，进度回调转发到 ProgressHub（WebSocket 广播）
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Dict, Any

from ..config.settings import AppConfig, ConfigManager
from ..core.llm_client import LLMClient
from ..core.pipeline import AnalysisPipeline
from ..progress_hub import get_hub
from ..utils.json_utils import safe_save_json

logger = logging.getLogger(__name__)

# 项目根目录（novel_analyzer_web/）：config.json 与 queue_state.json 落在这里
# PyInstaller 打包后 __file__ 指向临时解压目录，需特殊处理
if getattr(sys, "frozen", False):
    # 打包后：持久化数据写入用户目录（与 app.py 的 LOG_FILE 策略一致）
    PROJECT_ROOT = Path(os.environ.get("APPDATA", Path.home())) / "NovelAnalyzer"
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
else:
    # 与 app.py 保持一致：优先用 NOVEL_ROOT 指向“真实项目根”（共享盘），
    # 这样队列状态 / 配置 / 工作区数据都落在共享盘，而不是本地副本目录。
    _novel_root = os.environ.get("NOVEL_ROOT")
    PROJECT_ROOT = Path(_novel_root).resolve() if _novel_root else Path(__file__).resolve().parent.parent.parent
QUEUE_STATE_FILE = PROJECT_ROOT / "queue_state.json"
CONFIG_FILE = PROJECT_ROOT / "config.json"


def _store_path(p) -> str:
    """持久化时尽量存「相对 PROJECT_ROOT」的路径，让整个文件夹搬迁 / 换盘符 /
    换挂载点后仍能正确恢复（直接写死绝对路径会在移动后全部失效）。
    仅当路径确实位于 PROJECT_ROOT 之下时才存相对形式，否则退回绝对路径
    （兼容书库外置到其他盘的场景）。"""
    try:
        return Path(p).resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return Path(p).as_posix()


# H17 (2026-08-26) 发现流提取
# 停用词表：过滤"众人/他们/旁白"等指代词，避免"新人物"列表噪音
# 上线后从 api_failures / 用户反馈迭代词表；独立常量好改
_DISCOVERY_STOP_WORDS = frozenset({
    "众人", "他们", "对方", "旁白", "自己", "他", "她", "它", "我", "你",
    "我们", "你们", "它们", "她们", "他们俩", "他们三人", "某人", "何人",
    "此人", "那人", "大家", "无名", "路人", "某人影",
})


def _extract_discovery(result: Optional[dict], seen_characters: set) -> Optional[dict]:
    """从块结果提取发现流摘要（plan Task 1.2）

    4 类：
    - core_events 数
    - 新伏笔（foreshadowing[].clue 截 24 字，最多 5 条）
    - 新人物（首次登场：seen_characters 集合求差，最多 5 条）
    - 遗留悬念（cross_block.unresolved_questions 截 24 字，最多 2 条）

    跨块伏笔去重明确不做（plan 决策 5）。
    续跑后人物"全量首次"是已知限制（plan 标注 Open Question）。
    """
    if not isinstance(result, dict):
        return None
    core_events = result.get("core_events") or []
    foreshadowing = result.get("foreshadowing") or []
    cross_block = result.get("cross_block") or {}
    unresolved = cross_block.get("unresolved_questions") or []

    # 新人物：core_events[].characters 逗号/顿号分隔 → 拆词 → 过滤停用词/长度 → 已见集合求差
    new_characters: List[str] = []
    for ev in core_events:
        chars_str = ev.get("characters", "")
        if not chars_str:
            continue
        for name in str(chars_str).replace("、", ",").split(","):
            name = name.strip()
            if not name or len(name) < 2 or len(name) > 8:
                continue
            if name in _DISCOVERY_STOP_WORDS:
                continue
            if name in seen_characters:
                continue
            new_characters.append(name)
            seen_characters.add(name)

    return {
        "events": len(core_events),
        "foreshadows": [str(f.get("clue", ""))[:24] for f in foreshadowing][:5],
        "characters": new_characters[:5],
        "unresolved": [str(q)[:24] for q in unresolved][:2],
    }


def _load_path(s: str) -> Path:
    """读取时：相对路径按 PROJECT_ROOT 还原；绝对路径原样保留
    （兼容旧数据里的 F:/... 等绝对路径）。"""
    p = Path(s)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p


@dataclass
class QueueItem:
    """队列中的一个小说任务"""
    name: str                           # 书名
    blocks_dir: Path                    # 章节文件目录 (workspace/{书名}/blocks/)
    workspace_dir: Path                 # 工作区目录 (workspace/{书名}/)
    status: str = "pending"             # pending / running / done / failed / skipped
    total_chapters: int = 0
    completed_chapters: int = 0
    block_size: int = 1                 # 当前配置的 block_size（用于恢复时校验）
    start_time: float = 0.0
    end_time: float = 0.0
    error_message: str = ""
    archive_failed: bool = False        # 归档是否失败

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "blocks_dir": _store_path(self.blocks_dir),
            "workspace_dir": _store_path(self.workspace_dir),
            "status": self.status,
            "total_chapters": self.total_chapters,
            "completed_chapters": self.completed_chapters,
            "block_size": self.block_size,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "error_message": self.error_message,
            "archive_failed": self.archive_failed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueueItem":
        return cls(
            name=data.get("name", ""),
            blocks_dir=_load_path(data.get("blocks_dir", "")),
            workspace_dir=_load_path(data.get("workspace_dir", "")),
            status=data.get("status", "pending"),
            total_chapters=data.get("total_chapters", 0),
            completed_chapters=data.get("completed_chapters", 0),
            block_size=data.get("block_size", 1),
            start_time=data.get("start_time", 0.0),
            end_time=data.get("end_time", 0.0),
            error_message=data.get("error_message", ""),
            archive_failed=data.get("archive_failed", False),
        )


class QueueManager:
    """管理小说分析队列（自旧项目原样移植）"""

    def __init__(self):
        self._items: List[QueueItem] = []
        self._current_index: int = -1
        # check_api 的 heal 纠正值经此落盘持久化；由 AnalysisService 注入，
        # 独立构造的实例保持 None（跳过持久化，仅就地纠正）
        self.config_manager: Optional[ConfigManager] = None

    @property
    def items(self) -> List[QueueItem]:
        return self._items

    @property
    def count(self) -> int:
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        return len(self._items) == 0

    @property
    def is_finished(self) -> bool:
        if self.is_empty:
            return True
        return all(item.status in ("done", "skipped", "failed") for item in self._items)

    def add_item(self, item: QueueItem) -> int:
        # 去重：同 blocks_dir 不重复添加
        for existing in self._items:
            if existing.blocks_dir == item.blocks_dir:
                logger.info(f"队列跳过重复: {item.name} (blocks={item.blocks_dir})")
                return self._items.index(existing)
        self._items.append(item)
        logger.info(f"队列添加: {item.name} (blocks={item.blocks_dir})")
        return len(self._items) - 1

    def remove_item(self, index: int) -> Optional[QueueItem]:
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            if index < self._current_index:
                self._current_index -= 1
            elif index == self._current_index:
                self._current_index = min(self._current_index, len(self._items) - 1)
            logger.info(f"队列移除: {item.name}")
            return item
        return None

    def get_current(self) -> Optional[QueueItem]:
        if self.is_empty:
            return None
        if self._current_index < 0 or self._current_index >= len(self._items):
            self._current_index = self._find_next_pending()
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index]
        return None

    def advance(self) -> Optional[QueueItem]:
        if 0 <= self._current_index < len(self._items):
            current = self._items[self._current_index]
            if current.status == "running":
                current.status = "done"
            logger.info(f"队列推进: {current.name} -> {current.status}")

        self._current_index = self._find_next_pending()
        if self._current_index >= 0:
            return self._items[self._current_index]
        return None

    def mark_current(self, status: str, error_message: str = "") -> None:
        if 0 <= self._current_index < len(self._items):
            item = self._items[self._current_index]
            item.status = status
            if error_message:
                item.error_message = error_message
            logger.info(f"队列标记: {item.name} -> {status}")

    def move_up(self, index: int) -> bool:
        if 0 < index < len(self._items):
            self._items[index], self._items[index - 1] = self._items[index - 1], self._items[index]
            if self._current_index == index:
                self._current_index -= 1
            elif self._current_index == index - 1:
                self._current_index += 1
            return True
        return False

    def move_down(self, index: int) -> bool:
        if 0 <= index < len(self._items) - 1:
            self._items[index], self._items[index + 1] = self._items[index + 1], self._items[index]
            if self._current_index == index:
                self._current_index += 1
            elif self._current_index == index + 1:
                self._current_index -= 1
            return True
        return False

    def clear(self) -> None:
        self._items.clear()
        self._current_index = -1
        logger.info("队列已清空")

    def get_progress(self) -> Dict[str, Any]:
        completed_items = sum(1 for item in self._items if item.status == "done")
        current = self.get_current()
        # 单位统一为「块」：completed_chapters 是已完成的「块」数，total_chapters 是「章」数
        # （等于 blocks 目录下 N.txt 文件个数）。REST 兜底进度条必须与 WS 路径同单位，
        # 因此 total 也要换算成块数 ceil(章 / block_size)，否则把「块/章」混算后百分比严重失真。
        if current:
            bs = max(1, current.block_size)
            current_progress = current.completed_chapters
            current_total = (current.total_chapters + bs - 1) // bs
        else:
            current_progress = 0
            current_total = 0
        return {
            "total_items": len(self._items),
            "completed_items": completed_items,
            "current_item": current.name if current else "",
            "current_progress": current_progress,
            "current_total": current_total,
        }

    def scan_directory(self, base_dir: Path) -> List[QueueItem]:
        """扫描 {书名}/blocks/ 结构（网络盘优化：scandir + 线程池并行列举）"""
        base = Path(base_dir)
        if not base.is_dir():
            return []

        # 1) 顶层目录枚举：os.scandir 复用 d_type，避免逐目录额外 stat
        #    （网络盘下每次 stat 都是一次到服务器的往返，能省则省）
        subdirs = []
        with os.scandir(base) as it:
            for entry in it:
                if entry.is_dir():
                    subdirs.append(Path(entry.path))

        # 2) 每本书的 blocks/*.txt 列举并行化，把多本书的网络延迟重叠掉
        def _scan_one(subdir: Path) -> Optional[QueueItem]:
            blocks = subdir / "blocks"
            if not blocks.is_dir():
                return None
            txt_files = [f for f in blocks.glob("*.txt") if re.match(r"^\d+\.txt$", f.name)]
            if not txt_files:
                return None
            return QueueItem(
                name=subdir.name,
                blocks_dir=blocks,
                workspace_dir=subdir,
                total_chapters=len(txt_files),
            )

        items: List[QueueItem] = []
        if subdirs:
            max_workers = min(32, (os.cpu_count() or 4) + 4)
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for it in ex.map(_scan_one, subdirs):
                    if it is not None:
                        items.append(it)

        items.sort(key=lambda x: x.name)
        for it in items:
            logger.info(f"扫描到小说: {it.name} ({it.total_chapters}章)")
        return items

    def validate_item(self, item: QueueItem) -> bool:
        """校验 blocks 目录是否存在且有 txt 文件"""
        if not item.blocks_dir.exists():
            return False
        txt_files = [f for f in item.blocks_dir.glob("*.txt") if re.match(r"^\d+\.txt$", f.name)]
        return len(txt_files) >= 1

    async def check_api(self, config: AppConfig) -> bool:
        """轻量 API 预检查：优先用 /models 健康检查端点（不消耗对话 token、不污染日志），
        仅在 /models 不可用或返回空时回退到一次极短 chat 探针。
        原生协议两种探针均失败时，再以翻转 provider 各探一次（P2 2026-08-24：
        聚合网关常以 OpenAI 协议暴露 claude-* 模型，detect_provider 会误判为
        anthropic → /v1/messages 404 → auto 模式下整本书无提示全量失败）。"""
        # 空模型名直接拦截：带着空 model 发起请求只会得到晦涩的 API 报错
        if not str(config.api.model or '').strip():
            logger.warning("API 预检查失败：未配置模型（api.model 为空），请在设置页填写模型名")
            return False

        flipped = "openai" if (config.api.provider or "auto") in ("auto", "anthropic") else "anthropic"

        async def models_ok(provider: str) -> bool:
            try:
                models = await LLMClient.list_models(config.api.base_url, config.api.api_key, provider)
                return bool(models)
            except Exception as e:
                logger.debug(f"models 健康检查异常({provider}): {e}")
                return False

        async def chat_ok(provider: str) -> bool:
            try:
                client = LLMClient(replace(config.api, provider=provider))
                ok, _c, err, _t = await client.chat([{"role": "user", "content": "hi"}])
                if ok:
                    return True
                logger.debug(f"chat 探针失败({provider}): {err}")
                return False
            except Exception as e:
                logger.debug(f"chat 探针异常({provider}): {e}")
                return False

        native = (config.api.provider or "auto")

        # ① 原生协议 models → ② 原生 chat 探针
        if await models_ok(native):
            # 终审 M1 收口：/models 在 OpenAI 面成功且模型为 claude-* 时，
            # 即网关以 OpenAI 协议暴露 claude 系列——detect_provider 的前缀规则
            # 会把运行期请求误导向 /v1/messages（整书 404）。就地纠正并指引持久化。
            # 复审 Critical 护栏：官方 Anthropic 直连（base_url 含 anthropic）时
            # auto 解析本就命中 Anthropic 面 /models，探针成功≠协议误判，
            # 不得改写为 openai。仅聚合网关（URL 无 anthropic 特征）才纠正。
            if (native == "auto"
                    and str(config.api.model or "").lower().startswith("claude-")
                    and "anthropic" not in (config.api.base_url or "").lower()):
                config.api.provider = "openai"
                # 权威传播：config_manager 的内存单例 + 磁盘落盘。
                # 否则自动总结/手动总结经 config_manager.load() 重读磁盘时，
                # 纠正值丢失（复审 Important #2）。探针已验证该值可用，落盘安全；
                # save 内置 env-key 不回写保护，不会泄露密钥。
                try:
                    cm = getattr(self, "config_manager", None)
                    if cm is not None:
                        cm.config.api.provider = "openai"
                        cm.save()
                except Exception as e:
                    logger.warning(f"provider 纠正值持久化失败（本次运行仍生效）: {e}")
                logger.warning(
                    "检测到 OpenAI 兼容网关以 claude-* 模型提供服务：已就地纠正 "
                    "provider=openai 并写入配置持久化，"
                    "避免运行期请求被前缀规则误导向 /v1/messages。")
            return True
        logger.debug("models 健康检查不可用或为空，回退 chat 探针")
        if await chat_ok(native):
            return True

        # ③ 跨协议探针：翻转 provider 各试一次 models + chat，
        # 任一成功即认定协议误判——就地纠正（运行期对象，随后所有服务共用此
        # 配置对象即自动生效），并指引持久化；全败不改写。
        logger.warning(f"原生协议({native})探针均失败，尝试跨协议({flipped})探针…")
        if await models_ok(flipped) or await chat_ok(flipped):
            config.api.provider = flipped
            logger.warning(
                f"检测到协议误判：已就地纠正 provider={flipped}（本次运行生效）。"
                f"请在设置页保存配置以持久化，避免每次启动重复探测。")
            return True

        logger.warning("API 预检查失败：原生与跨协议探针均失败")
        return False

    def save_state(self, path: Path) -> None:
        """持久化队列状态（原子写，避免断电损坏 queue_state.json 丢失队列）"""
        state = {
            "current_index": self._current_index,
            "items": [item.to_dict() for item in self._items],
        }
        if safe_save_json(state, path, ensure_ascii=False):
            logger.info(f"队列状态已保存: {path}")
        else:
            logger.error(f"队列状态保存失败: {path}")

    def load_state(self, path: Path) -> None:
        """恢复队列状态"""
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self._items = [QueueItem.from_dict(d) for d in state.get("items", [])]
            self._current_index = state.get("current_index", -1)
            # 上次异常退出遗留的 running 状态重置为 pending（可续跑）
            for item in self._items:
                if item.status == "running":
                    item.status = "pending"
            logger.info(f"队列状态已恢复: {len(self._items)}项")
        except Exception as e:
            logger.error(f"队列状态恢复失败: {e}")

    def infer_progress(self, item: QueueItem) -> int:
        """扫描 output 目录推断已完成章数，校验 block_size"""
        output_dir = item.workspace_dir / "output"
        if not output_dir.exists():
            return 0
        valid_count = 0
        for f in output_dir.glob("*_result.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("block_size", 0) == item.block_size:
                    valid_count += 1
            except Exception:
                continue
        return valid_count

    def _find_next_pending(self) -> int:
        for i, item in enumerate(self._items):
            if item.status == "pending":
                return i
        return -1


class AnalysisService:
    """
    队列运行编排层（单例）。
    - start(): 启动队列消费 asyncio.Task，逐本运行 AnalysisPipeline
    - stop(): 停止当前 pipeline 并中断队列
    - 进度回调 → ProgressHub 广播（WS 消息 type 对齐旧 Qt 信号）
    """

    def __init__(self):
        self.config_manager = ConfigManager(CONFIG_FILE)
        self.config_manager.load()
        self.queue = QueueManager()
        # 复审 Important #2：check_api 的 heal 纠正值需经 config_manager 落盘
        # 持久化（check_api 以 self.queue 身份执行，getattr(self, "config_manager")
        # 取的就是这里挂上的引用）
        self.queue.config_manager = self.config_manager
        self.queue.load_state(QUEUE_STATE_FILE)
        self._runner_task: Optional[asyncio.Task] = None
        self._pipeline: Optional[AnalysisPipeline] = None
        self._stop_requested = False
        # 分类 token 累计 {category: {"input_tokens": int, "output_tokens": int}}
        self._token_stats: Dict[str, Dict[str, int]] = {}
        # 每章统计记录 [(chapter, elapsed, input_tokens, output_tokens)]
        self._chapter_stats: List[Dict[str, Any]] = []
        # H17 (2026-08-26) 发现流：跨块"已见人物"集合（断点续跑后全量"首次"是已知限制）
        self._seen_characters: set = set()
        self._total_retries = 0
        self._total_failed_tokens = 0
        # EFF-5：KV cache 命中 token 累计（运行中实时读当前 analyzer；结束后用累计值）
        self._total_cached_tokens = 0
        self._analysis_start_time: float = 0.0
        # 运行结束时刻（冻结耗时用：结束后 elapsed 不再随 time.time() 增长）
        self._analysis_end_time: Optional[float] = None
        # 注意：启动期自动扫描已迁移到 app.py 的 lifespan 启动阶段（asyncio.to_thread 执行），
        # 不再在此处同步执行——否则首个触发 get_service() 的 async 请求会在事件循环内
        # 同步跑完整工作区扫描（网络盘多书时卡数秒~数十秒），冻结整个事件循环。

    # ---- 状态 ----
    @property
    def is_running(self) -> bool:
        return self._runner_task is not None and not self._runner_task.done()

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running,
            "queue": self.queue.get_progress(),
            "items": [item.to_dict() for item in self.queue.items],
            "workspace_dir": self.workspace_path.as_posix(),
        }

    def token_stats(self) -> Dict[str, Any]:
        """返回分类 token 统计 + 每章记录 + 总耗时 + KV 缓存命中数（EFF-5）"""
        # 运行结束后用冻结的结束时刻计算，否则结束后的 elapsed 会无限增长
        if self._analysis_end_time:
            elapsed = self._analysis_end_time - self._analysis_start_time
        elif self._analysis_start_time:
            elapsed = time.time() - self._analysis_start_time
        else:
            elapsed = 0.0
        # KV cache 命中：运行中读当前 analyzer（实时）；结束后用累计值
        # getattr 兜底：部分单测用 __new__ 构造实例（无 __init__ 属性）
        cached_tokens = getattr(self, '_total_cached_tokens', 0)
        if self._pipeline is not None:
            analyzer = getattr(self._pipeline, '_analyzer', None)
            if analyzer is not None:
                try:
                    cached_tokens = analyzer.get_token_stats().get('cached_tokens', 0)
                except Exception:
                    cached_tokens = self._total_cached_tokens
        return {
            "categories": self._token_stats,
            "chapter_stats": self._chapter_stats[-200:],  # 最近200章
            "elapsed": elapsed,
            "running": self.is_running,
            "total_retries": self._total_retries,
            "total_failed_tokens": self._total_failed_tokens,
            "cached_tokens": cached_tokens or 0,
        }

    def _record_chapter_stat(self, payload: dict) -> None:
        """记录每章统计（含重试成本），供 /api/analysis/token_stats 展示"""
        self._chapter_stats.append({
            "chapter": payload.get("chapter", 0),
            "status": payload.get("status", "done"),
            "elapsed": payload.get("elapsed", 0),
            "input_tokens": payload.get("input_tokens", 0),
            "output_tokens": payload.get("output_tokens", 0),
            "retries": payload.get("retries", 0),
            "failed_tokens": payload.get("failed_tokens", 0),
        })
        self._total_retries += payload.get("retries", 0) or 0
        self._total_failed_tokens += payload.get("failed_tokens", 0) or 0

    @property
    def workspace_path(self) -> Path:
        """解析工作区绝对路径"""
        ws = self.config_manager.config.workspace_dir
        ws_path = Path(ws)
        if not ws_path.is_absolute():
            ws_path = PROJECT_ROOT / ws_path
        return ws_path

    def _auto_scan_workspace(self) -> None:
        """启动时自动扫描工作区目录，将小说加入队列"""
        ws = self.workspace_path
        if not ws.exists():
            ws.mkdir(parents=True, exist_ok=True)
            logger.info(f"已创建工作区目录: {ws}")
            return
        found = self.queue.scan_directory(ws)
        if not found:
            logger.info(f"工作区 {ws} 中未发现小说")
            return
        added = 0
        for item in found:
            item.block_size = max(1, self.config_manager.config.analysis.block_size)
            done_blocks = self.queue.infer_progress(item)
            item.completed_chapters = done_blocks
            idx = self.queue.add_item(item)
            if idx == len(self.queue.items) - 1:
                added += 1
        if added > 0:
            self.save_queue()
            logger.info(f"工作区自动扫描: 新增 {added} 本小说")

    def scan_workspace(self) -> int:
        """手动刷新工作区扫描（供前端调用）"""
        ws = self.workspace_path
        if not ws.exists():
            return 0
        found = self.queue.scan_directory(ws)
        added = 0
        for item in found:
            item.block_size = max(1, self.config_manager.config.analysis.block_size)
            done_blocks = self.queue.infer_progress(item)
            item.completed_chapters = done_blocks
            idx = self.queue.add_item(item)
            if idx == len(self.queue.items) - 1:
                added += 1
        if added > 0:
            self.save_queue()
        return added

    # ---- 控制 ----
    def start(self) -> bool:
        """启动队列分析（已在运行/队列空/总结运行中则返回 False）"""
        if self.is_running:
            logger.warning("分析已在运行中")
            return False
        # 双向互斥：总结运行期间启动分析会并发读写 output/，总结读到半成品数据
        # （summary_service.start 已拦分析中启动总结，此处补反向护栏）
        try:
            from .summary_service import get_summary_service as get_summary
            if get_summary().is_running:
                logger.warning("总结任务运行中，不能启动分析")
                return False
        except Exception:
            pass
        if self.queue.is_empty or self.queue.is_finished:
            logger.warning("队列为空或已全部完成")
            return False
        self._stop_requested = False
        self._token_stats = {}
        self._chapter_stats = []
        self._total_retries = 0
        self._total_failed_tokens = 0
        self._total_cached_tokens = 0
        # P1-b (2026-08-26)：重置"已见人物"集合，避免上一本书的人物污染新书首次登场判定
        # （AnalysisService 是进程级单例，__init__ 只跑一次）
        self._seen_characters = set()
        self._analysis_start_time = time.time()
        self._analysis_end_time = None
        self._runner_task = asyncio.create_task(self._run_queue())
        return True

    def stop(self) -> bool:
        """请求停止：当前 pipeline 在下一检查点退出，队列不再推进"""
        if not self.is_running:
            return False
        self._stop_requested = True
        if self._pipeline is not None:
            self._pipeline.stop()
        return True

    def save_queue(self) -> None:
        self.queue.save_state(QUEUE_STATE_FILE)

    # ---- 队列消费循环 ----
    async def _run_queue(self) -> None:
        hub = get_hub()
        config = self.config_manager.load()  # 每次启动重读配置

        try:
            # API 预检查
            await hub.log("API 预检查中...")
            if not await self.queue.check_api(config):
                await hub.log("API 预检查失败，请检查设置中的 API 配置", level="error")
                await hub.state_change("idle", "API 预检查失败")
                return

            await hub.state_change("running", "队列分析开始")

            while not self._stop_requested:
                item = self.queue.get_current()
                if item is None or item.status not in ("pending", "running"):
                    break
                if not self.queue.validate_item(item):
                    self.queue.mark_current("failed", "blocks 目录无效")
                    await hub.log(f"《{item.name}》blocks 目录无效，跳过", level="error")
                    self.queue.advance()
                    self.save_queue()
                    continue

                await self._run_one_item(item, config)
                if self._stop_requested:
                    break
                self.queue.advance()
                self.save_queue()

            # 收尾
            self.save_queue()
            if self._stop_requested:
                await hub.state_change("stopped", "分析已停止")
            else:
                # 2026-08-02 spec：队列全部完成且开启 auto_summary 时，
                # 对已完成的书逐本串行执行最终总结（单本失败记录日志继续下一本；
                # 期间用户可通过既有 /api/summary/stop 停止）
                if config.analysis.auto_summary:
                    await self._auto_summary_done_books(config)
                # P2 修复：自动总结期间用户可能已停止——此时不得广播“队列全部完成”
                if self._stop_requested:
                    await hub.state_change("stopped", "分析已停止（总结阶段中止）")
                else:
                    await hub.state_change("done", "队列全部完成")
            self._pipeline = None
        finally:
            # 冻结结束时刻：任务完成/被取消/异常后 is_running 即变 False，
            # 此时 token_stats 的 elapsed 必须停止增长
            self._analysis_end_time = time.time()

    async def _auto_summary_done_books(self, config: AppConfig) -> None:
        """队列中 status==done 的书逐本自动最终总结（延迟导入避免循环依赖：
        summary_service 顶层 import 本模块的 get_service）。"""
        from .summary_service import get_summary_service as get_summary

        hub = get_hub()
        done_books = [item.name for item in self.queue.items if item.status == "done"]
        if not done_books:
            return
        await hub.log(f"队列完成，自动总结 {len(done_books)} 本书...")
        summary = get_summary()
        batch_size = max(5, config.analysis.summary_batch_size)
        concurrency = max(1, config.analysis.summary_concurrency)
        for book_id in done_books:
            if self._stop_requested:
                await hub.log("自动总结已停止（用户停止）", level="warn")
                return
            try:
                summary.start(book_id, 1, 99999, batch_size, concurrency,
                              allow_during_analysis=True)
                await hub.log(f"自动总结开始: {book_id}")
                # 等待本书总结完成（轮询；不阻塞其它逻辑，逐本串行保证 API 并发可控）
                while summary.is_running:
                    if self._stop_requested:
                        summary.stop()
                        break
                    await asyncio.sleep(1.0)
                if summary.status().get("phase") == "complete":
                    await hub.log(f"自动总结完成: {book_id}")
                else:
                    await hub.log(f"自动总结未完成或失败: {book_id}（{summary.status().get('error', '')}）", level="warn")
            except Exception as e:
                await hub.log(f"自动总结失败（{book_id}）: {e}", level="error")
        self.save_queue()

    async def _run_one_item(self, item: QueueItem, base_config: AppConfig) -> None:
        """运行单本书的完整分析"""
        hub = get_hub()

        # 每本书克隆配置：working_directory 指向该书工作区（output/ 落在其中）
        config = AppConfig.from_dict(base_config.to_dict())
        config.working_directory = str(item.workspace_dir)
        knowledge_file = item.workspace_dir / config.knowledge_file

        item.status = "running"
        item.block_size = max(1, config.analysis.block_size)
        item.start_time = time.time()
        self.save_queue()
        await hub.log(f"开始分析《{item.name}》（共{item.total_chapters}章）")
        await hub.state_change("running", f"分析中: {item.name}")

        async def on_progress(payload: dict) -> None:
            """pipeline 进度 → WS 消息（type 对齐旧 Qt 信号）

            H17 (2026-08-26) 改造：
            - status=start → 转发 block_start（带 range 文本）
            - block_done 补 range 字段
            - status=done 且 result 存在 → publish discovery
            """
            status = payload.get("status", "")
            message = payload.get("message", "")
            if message:
                level = "error" if status in ("failed", "failed_summary") else "info"
                await hub.log(message, level=level)
            if "progress" in payload and "total" in payload:
                item.completed_chapters = payload["progress"]
                await hub.progress(payload["progress"], payload["total"])

            # H17: block_start 转发（带章范围文本 message 复用现有"开始分析第X-Y章..."）
            if status == "start":
                await hub.publish({
                    "type": "block_start",
                    "payload": {
                        "chapter": payload.get("chapter", 0),  # block_id（块起始章号）
                        "range": message,
                        "progress": payload.get("progress", 0),
                        "total": payload.get("total", 0),
                        "ts": time.time(),
                    },
                })

            if status in ("done", "failed"):
                await hub.publish({
                    "type": "block_done",
                    "payload": {
                        "chapter": payload.get("chapter", 0),
                        "ok": status == "done",
                        "range": message,  # H17: 失败/完成 message 含章范围
                        "elapsed": payload.get("elapsed"),
                        "tokens": payload.get("tokens"),
                    },
                })
                # 记录每章统计（失败块同样计入：重试成本不可因失败而归零）
                self._record_chapter_stat(payload)

                # H17: discovery 提取（仅成功块，result 是 AnalysisResult dataclass dict）
                if status == "done":
                    result = payload.get("result")
                    discovery = _extract_discovery(result, self._seen_characters)
                    if discovery:
                        await hub.publish({"type": "discovery", "payload": discovery})

        async def on_token_stats(payload: dict) -> None:
            # 累积分类 token 统计
            cat = payload.get("category", "unknown")
            if cat not in self._token_stats:
                self._token_stats[cat] = {"input_tokens": 0, "output_tokens": 0}
            # 直接使用 input_tokens/output_tokens（不再用 or 回退到 current_tokens，
            # 因为 input_tokens=0 是合法值，or 会错误回退到 input+output 之和）
            self._token_stats[cat]["input_tokens"] += payload.get("input_tokens", 0) or 0
            self._token_stats[cat]["output_tokens"] += payload.get("output_tokens", 0) or 0
            await hub.publish({"type": "token_stats", "payload": payload})

        # token 落盘基线快照（2026-08-17）：本书开始前的累计值，finally 中做差得本书消耗
        stats_baseline = {
            "chapter_stats_len": len(self._chapter_stats),
            "categories": {k: dict(v) for k, v in self._token_stats.items()},
            "cached": self._total_cached_tokens,
            "retries": self._total_retries,
            "failed_tokens": self._total_failed_tokens,
        }

        self._pipeline = AnalysisPipeline(
            config=config,
            directory=item.blocks_dir,
            knowledge_file=knowledge_file,
            on_progress=on_progress,
            on_token_stats=on_token_stats,
        )

        try:
            result = await self._pipeline.run()
            if result.get("stopped"):
                item.status = "pending"  # 停止的书保持可续跑
                await hub.log(f"《{item.name}》分析已停止（可续跑）", level="warn")
            elif result.get("failed_chapters"):
                item.status = "done"  # 有失败块但整体完成
                item.error_message = f"失败{len(result['failed_chapters'])}块"
                await hub.log(
                    f"《{item.name}》完成，但有失败块: {result['failed_chapters']}",
                    level="warn")
            elif result.get("skipped_chapters"):
                item.status = "done"  # 有拦截跳过但整体完成
                item.error_message = f"内容审核拦截{len(result['skipped_chapters'])}块"
                await hub.log(
                    f"《{item.name}》完成，{len(result['skipped_chapters'])}块被内容审核拦截跳过: "
                    f"{result['skipped_chapters']}",
                    level="warn")
            else:
                item.status = "done"
                await hub.log(f"《{item.name}》分析完成（{result.get('total_analyzed', 0)}块）")
        except Exception as e:
            logger.error(f"《{item.name}》分析异常: {e}", exc_info=True)
            item.status = "failed"
            item.error_message = str(e)
            await hub.log(f"《{item.name}》分析异常: {e}", level="error")
        finally:
            item.end_time = time.time()
            # EFF-5：pipeline 置 None 前累计本书 KV 缓存命中数（结束后 token_stats 仍可见）
            if self._pipeline is not None:
                analyzer = getattr(self._pipeline, '_analyzer', None)
                if analyzer is not None:
                    try:
                        self._total_cached_tokens += analyzer.get_token_stats().get('cached_tokens', 0) or 0
                    except Exception:
                        pass
            self._pipeline = None
            self._persist_book_token_stats(item, stats_baseline)

        # 完成后自动归档（可选）
        if item.status == "done" and self.config_manager.config.analysis.auto_archive:
            await asyncio.to_thread(self._archive_item, item)

    def _persist_book_token_stats(self, item: QueueItem, baseline: Dict[str, Any]) -> None:
        """本书分析 token 消耗落盘 output/token_stats.json（重启后可查历史成本）。
        失败仅记日志，不影响主流程。"""
        try:
            from ..utils.json_utils import safe_save_json
            output_dir = item.workspace_dir / "output"
            if not output_dir.exists():
                return
            categories = {}
            for cat, v in self._token_stats.items():
                before = baseline["categories"].get(cat, {"input_tokens": 0, "output_tokens": 0})
                categories[cat] = {
                    "input_tokens": v["input_tokens"] - before["input_tokens"],
                    "output_tokens": v["output_tokens"] - before["output_tokens"],
                }
            payload = {
                "type": "analysis",
                "book_id": item.name,
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "elapsed": (item.end_time or time.time()) - (item.start_time or time.time()),
                "categories": categories,
                "total_retries": self._total_retries - baseline["retries"],
                "total_failed_tokens": self._total_failed_tokens - baseline["failed_tokens"],
                "cached_tokens": self._total_cached_tokens - baseline["cached"],
                "chapter_stats": self._chapter_stats[baseline["chapter_stats_len"]:],
            }
            safe_save_json(payload, output_dir / "token_stats.json")
        except Exception as e:
            logger.warning(f"《{item.name}》token 统计落盘失败: {e}")

    def _archive_item(self, item: QueueItem) -> None:
        """归档整个小说目录到 base_dir/分析结果/（失败仅标记，不影响主流程）"""
        try:
            archive_root = item.workspace_dir.parent / "分析结果"
            archive_root.mkdir(parents=True, exist_ok=True)
            target = archive_root / item.workspace_dir.name
            if target.exists():
                raise FileExistsError(f"归档目标已存在: {target}")
            shutil.move(str(item.workspace_dir), str(target))
            logger.info(f"《{item.name}》已归档到 {target}")
        except Exception as e:
            item.archive_failed = True
            logger.warning(f"《{item.name}》归档失败: {e}")


# 模块级单例
_service: Optional[AnalysisService] = None


def get_service() -> AnalysisService:
    global _service
    if _service is None:
        _service = AnalysisService()
    return _service
