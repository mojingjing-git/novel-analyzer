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
from dataclasses import dataclass
from pathlib import Path
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
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
QUEUE_STATE_FILE = PROJECT_ROOT / "queue_state.json"
CONFIG_FILE = PROJECT_ROOT / "config.json"


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
            "blocks_dir": self.blocks_dir.as_posix(),
            "workspace_dir": self.workspace_dir.as_posix(),
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
            blocks_dir=Path(data.get("blocks_dir", "")),
            workspace_dir=Path(data.get("workspace_dir", "")),
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
        """扫描 {书名}/blocks/ 结构"""
        items = []
        if not base_dir.exists() or not base_dir.is_dir():
            return items

        for subdir in sorted(base_dir.iterdir()):
            if not subdir.is_dir():
                continue
            blocks = subdir / "blocks"
            if not blocks.exists():
                continue
            txt_files = [f for f in blocks.glob("*.txt") if re.match(r"^\d+\.txt$", f.name)]
            if not txt_files:
                continue
            item = QueueItem(
                name=subdir.name,
                blocks_dir=blocks,
                workspace_dir=subdir,
                total_chapters=len(txt_files),
            )
            items.append(item)
            logger.info(f"扫描到小说: {subdir.name} ({len(txt_files)}章)")

        return items

    def validate_item(self, item: QueueItem) -> bool:
        """校验 blocks 目录是否存在且有 txt 文件"""
        if not item.blocks_dir.exists():
            return False
        txt_files = [f for f in item.blocks_dir.glob("*.txt") if re.match(r"^\d+\.txt$", f.name)]
        return len(txt_files) >= 1

    async def check_api(self, config: AppConfig) -> bool:
        """轻量 API 预检查：优先用 /models 健康检查端点（不消耗对话 token、不污染日志），
        仅在 /models 不可用或返回空时回退到一次极短 chat 探针。"""
        # 空模型名直接拦截：带着空 model 发起请求只会得到晦涩的 API 报错
        if not str(config.api.model or '').strip():
            logger.warning("API 预检查失败：未配置模型（api.model 为空），请在设置页填写模型名")
            return False
        try:
            models = await LLMClient.list_models(config.api.base_url, config.api.api_key)
            if models:
                return True
        except Exception as e:
            logger.debug(f"models 健康检查异常: {e}")
        # 回退探针：仅一次、内容极短。
        # 注意 chat() 不会为 API 错误抛异常（所有失败路径都返回 (False, ...)，见 llm_client.py），
        # 所以必须检查返回值，不能无条件 return True，否则预检查形同虚设。
        try:
            client = LLMClient(config.api)
            ok, _content, err, _tokens = await client.chat([{"role": "user", "content": "hi"}])
            if ok:
                return True
            logger.warning(f"API 预检查（chat 探针）返回失败: {err}")
            return False
        except Exception as e:
            logger.warning(f"API 预检查失败: {e}")
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
        self.queue.load_state(QUEUE_STATE_FILE)
        self._runner_task: Optional[asyncio.Task] = None
        self._pipeline: Optional[AnalysisPipeline] = None
        self._stop_requested = False
        # 分类 token 累计 {category: {"input_tokens": int, "output_tokens": int}}
        self._token_stats: Dict[str, Dict[str, int]] = {}
        # 每章统计记录 [(chapter, elapsed, input_tokens, output_tokens)]
        self._chapter_stats: List[Dict[str, Any]] = []
        self._total_retries = 0
        self._total_failed_tokens = 0
        self._analysis_start_time: float = 0.0
        # 运行结束时刻（冻结耗时用：结束后 elapsed 不再随 time.time() 增长）
        self._analysis_end_time: Optional[float] = None
        # 启动时自动扫描工作区
        self._auto_scan_workspace()

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
        """返回分类 token 统计 + 每章记录 + 总耗时"""
        # 运行结束后用冻结的结束时刻计算，否则结束后的 elapsed 会无限增长
        if self._analysis_end_time:
            elapsed = self._analysis_end_time - self._analysis_start_time
        elif self._analysis_start_time:
            elapsed = time.time() - self._analysis_start_time
        else:
            elapsed = 0.0
        return {
            "categories": self._token_stats,
            "chapter_stats": self._chapter_stats[-200:],  # 最近200章
            "elapsed": elapsed,
            "running": self.is_running,
            "total_retries": self._total_retries,
            "total_failed_tokens": self._total_failed_tokens,
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
        """启动队列分析（已在运行/队列空则返回 False）"""
        if self.is_running:
            logger.warning("分析已在运行中")
            return False
        if self.queue.is_empty or self.queue.is_finished:
            logger.warning("队列为空或已全部完成")
            return False
        self._stop_requested = False
        self._token_stats = {}
        self._chapter_stats = []
        self._total_retries = 0
        self._total_failed_tokens = 0
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
                await hub.state_change("done", "队列全部完成")
            self._pipeline = None
        finally:
            # 冻结结束时刻：任务完成/被取消/异常后 is_running 即变 False，
            # 此时 token_stats 的 elapsed 必须停止增长
            self._analysis_end_time = time.time()

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
            """pipeline 进度 → WS 消息（type 对齐旧 Qt 信号）"""
            status = payload.get("status", "")
            message = payload.get("message", "")
            if message:
                level = "error" if status in ("failed", "failed_summary") else "info"
                await hub.log(message, level=level)
            if "progress" in payload and "total" in payload:
                item.completed_chapters = payload["progress"]
                await hub.progress(payload["progress"], payload["total"])
            if status in ("done", "failed"):
                await hub.publish({
                    "type": "block_done",
                    "payload": {
                        "chapter": payload.get("chapter", 0),
                        "ok": status == "done",
                        "elapsed": payload.get("elapsed"),
                        "tokens": payload.get("tokens"),
                    },
                })
                # 记录每章统计（失败块同样计入：重试成本不可因失败而归零）
                self._record_chapter_stat(payload)

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
            self._pipeline = None

        # 完成后自动归档（可选）
        if item.status == "done" and self.config_manager.config.analysis.auto_archive:
            await asyncio.to_thread(self._archive_item, item)

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
