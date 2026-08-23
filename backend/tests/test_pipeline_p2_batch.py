"""P2 批量修复验证：
1. MemoryState.failed_chapters_in 按 valid_block_ids 过滤（跨场失败标记不再误报）
2. _archive_momentum_to_milestone 对非 str 势头条目不再 TypeError
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.core.memory_state import MemoryState
from backend.core.pipeline import AnalysisPipeline


def test_failed_chapters_in_filters_by_scope():
    state = MemoryState()
    state._failed_chapters.update({95: "旧场失败", 3: "本轮失败"})
    assert state.failed_chapters_in({3, 7, 11}) == [3]
    assert state.failed_chapters_in(set()) == []


class _FakeRollingClient:
    async def chat_with_retry(self, messages, max_tokens=None):
        return True, "ch1-3: 测试里程碑", "", (0, 0), {}


def test_archive_momentum_tolerates_non_str_entries():
    p = AnalysisPipeline.__new__(AnalysisPipeline)

    async def _noop_emit(tokens):
        pass

    p._emit_rolling_tokens = _noop_emit

    structured = {"global_milestones": ["ch1: 起点", {"text": "dict 条目"}],
                  "recent_momentum": []}
    momentum = [{"text": "dict 势头"}, "ch5: 正常条目"]

    # 此前 "\n".join(momentum) 对 dict 元素直接 TypeError
    asyncio.run(p._archive_momentum_to_milestone(_FakeRollingClient(), structured, momentum))

    assert structured["recent_momentum"] == []
    assert any(isinstance(m, str) and "测试里程碑" in m for m in structured["global_milestones"])
