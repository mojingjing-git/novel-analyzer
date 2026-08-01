"""
测试 ProgressHub WebSocket 消息广播
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.progress_hub import ProgressHub


async def test_subscribe_unsubscribe():
    """订阅和取消订阅"""
    hub = ProgressHub()
    q1 = await hub.subscribe()
    q2 = await hub.subscribe()
    assert len(hub._queues) == 2
    await hub.unsubscribe(q1)
    assert len(hub._queues) == 1
    await hub.unsubscribe(q2)
    assert len(hub._queues) == 0
    print("✅ test_subscribe_unsubscribe passed")


async def test_publish_to_all():
    """广播消息到所有订阅者"""
    hub = ProgressHub()
    q1 = await hub.subscribe()
    q2 = await hub.subscribe()
    msg = {"type": "log", "payload": {"level": "info", "text": "hello"}}
    await hub.publish(msg)
    m1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    m2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert m1 == msg
    assert m2 == msg
    await hub.unsubscribe(q1)
    await hub.unsubscribe(q2)
    print("✅ test_publish_to_all passed")


async def test_no_subscribers():
    """无订阅者时发布不报错"""
    hub = ProgressHub()
    msg = {"type": "progress", "payload": {"current": 1, "total": 10}}
    await hub.publish(msg)
    print("✅ test_no_subscribers passed")


async def test_unsubscribe_nonexistent():
    """取消不存在的订阅不报错"""
    hub = ProgressHub()
    q = asyncio.Queue()
    await hub.unsubscribe(q)
    assert len(hub._queues) == 0
    print("✅ test_unsubscribe_nonexistent passed")


async def main():
    await test_subscribe_unsubscribe()
    await test_publish_to_all()
    await test_no_subscribers()
    await test_unsubscribe_nonexistent()
    print("\n🎉 All progress_hub tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
