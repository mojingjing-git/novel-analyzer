"""
最终总结编排服务（单例）
- start(): 为指定书启动 FinalSummaryRunner asyncio.Task
- stop(): 请求停止（重试链下一检查点退出）
- 进度回调 → ProgressHub 广播（WS 消息 type=summary_progress / token_stats）
- 完成后报告写入 output_dir/final_summary_report.md（对齐旧 main_window 行为）
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any

from ..config.settings import AppConfig
from ..progress_hub import get_hub
from .final_summary import FinalSummaryRunner
from .queue_service import get_service as get_analysis_service
from . import book_service

logger = logging.getLogger(__name__)


class SummaryService:
    """最终总结运行编排层（单例，同一时刻只允许一个总结任务）"""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._runner: Optional[FinalSummaryRunner] = None
        self._book_id: str = ""
        self._phase: str = "idle"
        self._error: str = ""
        self._batches_done: int = 0
        self._total_batches: int = 0
        self._started_at: float = 0.0
        self._finished_at: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running,
            "book_id": self._book_id,
            "phase": self._phase,
            "batches_done": self._batches_done,
            "total_batches": self._total_batches,
            "error": self._error,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "token_stats": self._runner.get_token_stats() if self._runner else {},
        }

    def start(self, book_id: str, start_chapter: int, end_chapter: int,
              batch_size: int, concurrency: Optional[int] = None) -> None:
        """启动总结任务。已在运行抛 RuntimeError，书不存在抛 KeyError，无结果抛 ValueError"""
        if self.is_running:
            raise RuntimeError("总结任务已在运行中")
        # 并发护栏：分析正在运行时启动总结会读取半成品 chapter_*_result.json，导致报告错乱
        if get_analysis_service().is_running:
            raise RuntimeError("分析任务正在进行中，请先停止或等待其完成后再启动最终总结")

        output_dir = book_service.get_output_dir(book_id)
        if output_dir is None:
            raise KeyError(f"书目不存在: {book_id}")
        if not output_dir.exists() or not any(output_dir.glob("chapter_*_result.json")):
            raise ValueError(f"《{book_id}》尚无章节分析结果")

        # 每次启动重读配置（与 AnalysisService 一致）
        config: AppConfig = get_analysis_service().config_manager.load()
        if concurrency is None:
            concurrency = config.analysis.concurrency

        self._book_id = book_id
        self._phase = "starting"
        self._error = ""
        self._batches_done = 0
        self._total_batches = 0
        self._started_at = time.time()
        self._finished_at = 0.0

        hub = get_hub()

        def on_progress(payload: dict) -> None:
            """runner 进度（事件循环内的同步回调）→ 状态更新 + WS 广播"""
            ptype = payload.get("type", "")
            if ptype == "phase":
                self._phase = payload.get("phase", self._phase)
                self._total_batches = payload.get("total_batches", self._total_batches)
            elif ptype == "batch_done":
                self._batches_done += 1
                self._total_batches = payload.get("total_batches", self._total_batches)
            elif ptype == "complete":
                self._phase = "complete"
            message = payload.get("message", "")
            if message:
                level = "error" if ptype == "batch_failed" else "info"
                asyncio.create_task(hub.log(message, level=level))
            asyncio.create_task(hub.publish({
                "type": "summary_progress",
                "payload": {**payload, "book_id": book_id,
                            "batches_done": self._batches_done},
            }))

        def on_token_stats(payload: dict) -> None:
            asyncio.create_task(hub.publish({
                "type": "token_stats",
                "payload": {**payload, "source": "summary"},
            }))

        self._runner = FinalSummaryRunner(
            config=config,
            output_dir=output_dir,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            batch_size=batch_size,
            concurrency=concurrency,
            on_progress=on_progress,
            on_token_stats=on_token_stats,
        )
        self._task = asyncio.create_task(self._run(output_dir))

    def stop(self) -> bool:
        if not self.is_running:
            return False
        if self._runner is not None:
            self._runner.stop()
        return True

    async def _run(self, output_dir: Path) -> None:
        hub = get_hub()
        await hub.state_change("summary_running", f"最终总结开始: {self._book_id}")
        try:
            report = await self._runner.run()
            if report is None:
                self._phase = "stopped"
                await hub.state_change("summary_stopped", f"总结已停止: {self._book_id}")
            else:
                report_path = output_dir / "final_summary_report.md"
                await asyncio.to_thread(report_path.write_text, report, encoding="utf-8")
                logger.info(f"全书脉络报告已保存: {report_path}")
                self._phase = "complete"
                await hub.state_change("summary_done", f"总结完成: {self._book_id}")
        except Exception as e:
            logger.error(f"最终总结异常: {e}", exc_info=True)
            self._phase = "failed"
            self._error = str(e)
            await hub.log(f"最终总结失败: {e}", level="error")
            await hub.state_change("summary_failed", str(e))
        finally:
            self._finished_at = time.time()


# 模块级单例
_service: Optional[SummaryService] = None


def get_summary_service() -> SummaryService:
    global _service
    if _service is None:
        _service = SummaryService()
    return _service
