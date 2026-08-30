"""
ProgressHub 队列满分级驱逐测试（车道堆叠修复）

验证 publish() 在慢消费者打满队列时：
- 优先驱逐 token_delta（高频可再生）
- 其次驱逐 log
- 绝不驱逐 block_start/block_done/progress 等关键事件（除非队列里只剩它们）
"""
import asyncio

import pytest

from backend.progress_hub import ProgressHub


def _fill(q: asyncio.Queue, n: int, msg: dict) -> None:
    for _ in range(n):
        q.put_nowait(dict(msg))


def _types(q: asyncio.Queue) -> list:
    return [m["type"] for m in list(q._queue)]


@pytest.mark.asyncio
async def test_eviction_prefers_token_delta_over_block_done():
    """满队 token_delta 中插入 block_done：丢一条 token_delta，block_done 完整保留"""
    hub = ProgressHub()
    q = await hub.subscribe()
    _fill(q, 1000, {"type": "token_delta", "payload": {"unit_idx": 1}})
    await hub.publish({"type": "block_done", "payload": {"chapter": 9}})
    types = _types(q)
    assert len(types) == 1000
    assert types.count("block_done") == 1
    assert types.count("token_delta") == 999


@pytest.mark.asyncio
async def test_eviction_prefers_log_over_critical():
    """满队 log 中插入 block_start：丢 log，不丢关键事件"""
    hub = ProgressHub()
    q = await hub.subscribe()
    _fill(q, 1000, {"type": "log", "payload": {}})
    await hub.publish({"type": "block_start", "payload": {"chapter": 1}})
    types = _types(q)
    assert types.count("log") == 999
    assert types.count("block_start") == 1


@pytest.mark.asyncio
async def test_eviction_all_critical_drops_oldest():
    """满队全是关键事件时退回丢最旧（先进先出语义）"""
    hub = ProgressHub()
    q = await hub.subscribe()
    for i in range(1000):
        q.put_nowait({"type": "block_done", "payload": {"chapter": i}})
    await hub.publish({"type": "block_start", "payload": {"chapter": -1}})
    msgs = list(q._queue)
    assert len(msgs) == 1000
    chapters = [m["payload"]["chapter"] for m in msgs if m["type"] == "block_done"]
    # 最早的 chapter=0 被驱逐，剩余从 1 开始且连续
    assert min(chapters) == 1
    assert msgs[-1]["type"] == "block_start"
