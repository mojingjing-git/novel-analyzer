"""P1 修复验证（2026-08-24）：stop() 必须 cancel 风格提取任务——
该任务持有独立 LLMClient，不受 _llm.request_stop() 控制。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import AppConfig
from backend.services.final_summary import FinalSummaryRunner


def _make_runner(tmp_path):
    return FinalSummaryRunner(
        config=AppConfig(), output_dir=tmp_path,
        start_chapter=1, end_chapter=4, batch_size=2, concurrency=2,
        on_progress=lambda p: None, on_token_stats=lambda p: None)


def test_stop_cancels_running_style_task(tmp_path):
    runner = _make_runner(tmp_path)
    started = asyncio.Event()

    async def hang_forever():
        started.set()
        await asyncio.sleep(60)   # 模拟在途 LLM 调用（带完整重试链）

    async def main():
        runner._style_task = asyncio.create_task(hang_forever())
        await asyncio.wait_for(started.wait(), timeout=2)
        runner.stop()             # 此前 stop() 对该任务无任何作用
        try:
            await asyncio.wait_for(runner._style_task, timeout=3)
        except asyncio.CancelledError:
            # 任务被 stop() 取消时 CancelledError 会穿透 wait_for 传播到等待方，
            # 属预期行为；能在此收到取消即证明修复生效
            pass

    asyncio.run(main())
    assert runner._style_task.cancelled()


def test_stop_is_noop_without_style_task(tmp_path):
    runner = _make_runner(tmp_path)
    runner.stop()                 # 不应抛 AttributeError
    assert runner._stop_requested is True
