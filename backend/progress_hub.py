"""
进度推送中枢（WebSocket 广播）

统一的进度通道，替代旧项目的 Qt 信号槽。分析任务通过 ProgressHub.publish()
发布消息，所有连接的 WebSocket 客户端都会收到。

消息格式（对齐旧 Qt 信号）：
    {"type": "log",          "payload": {"level": "info", "text": "..."}}
    {"type": "progress",     "payload": {"current": 10, "total": 100, "eta": "..."}}
    {"type": "block_done",   "payload": {"chapter": 5, "ok": true}}
    {"type": "state_change", "payload": {"state": "running|stopped|done", "detail": "..."}}
    {"type": "token_stats",  "payload": {"category": "chapter", "input_tokens": 100, "output_tokens": 50}}
"""

import asyncio
import logging
import queue as thread_queue
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)


class ProgressHub:
    """异步进度广播中枢（单用户桌面应用，单例）"""

    def __init__(self) -> None:
        # 每个连接持有一个独立队列，publish 时向所有队列投递
        self._queues: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        """新连接订阅，返回其专属消息队列"""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._queues.add(q)
        logger.debug("WS 订阅者接入，当前 %d 个", len(self._queues))
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """连接断开时注销队列"""
        async with self._lock:
            self._queues.discard(q)
        logger.debug("WS 订阅者断开，当前 %d 个", len(self._queues))

    async def publish(self, message: Dict[str, Any]) -> None:
        """向所有订阅者广播一条消息"""
        async with self._lock:
            targets = list(self._queues)
        for q in targets:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # 慢消费者：丢弃最旧消息后重试，避免阻塞发布方
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except Exception:
                    pass

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, message: Dict[str, Any]) -> None:
        """供非事件循环线程调用（如同步代码通过 to_thread 运行时回推进度）"""
        asyncio.run_coroutine_threadsafe(self.publish(message), loop)

    # ---- 便捷方法 ----
    async def log(self, text: str, level: str = "info") -> None:
        await self.publish({"type": "log", "payload": {"level": level, "text": text}})

    async def progress(self, current: int, total: int, eta: str = "") -> None:
        await self.publish({"type": "progress", "payload": {"current": current, "total": total, "eta": eta}})

    async def block_done(self, chapter: int, ok: bool = True) -> None:
        await self.publish({"type": "block_done", "payload": {"chapter": chapter, "ok": ok}})

    async def state_change(self, state: str, detail: str = "") -> None:
        await self.publish({"type": "state_change", "payload": {"state": state, "detail": detail}})


# 模块级单例
_hub: ProgressHub | None = None


def get_hub() -> ProgressHub:
    global _hub
    if _hub is None:
        _hub = ProgressHub()
    return _hub


# ==================== Python logging → WebSocket 转发 ====================

class HubLogHandler(logging.Handler):
    """
    将 Python logging 输出转发到 ProgressHub（WebSocket 广播）。
    使用线程安全队列，兼容 asyncio.to_thread 内的同步代码。
    """

    def __init__(self):
        super().__init__()
        self._q: thread_queue.Queue = thread_queue.Queue(maxsize=3000)

    def emit(self, record: logging.LogRecord) -> None:
        # 过滤自身和 uvicorn 内部日志，避免循环/噪音
        if record.name.startswith(("backend.progress_hub", "uvicorn", "httpx", "httpcore")):
            return
        try:
            msg = self.format(record)
            level = (
                "error" if record.levelno >= logging.ERROR
                else "warn" if record.levelno >= logging.WARNING
                else "info"
            )
            self._q.put_nowait({"type": "log", "payload": {"level": level, "text": msg}})
        except thread_queue.Full:
            pass  # 队列满时丢弃，不阻塞业务线程
        except Exception:
            pass


async def _forward_logs(handler: HubLogHandler, hub: ProgressHub) -> None:
    """后台任务：从线程安全队列取日志，转发到 WebSocket（100ms 轮询）"""
    while True:
        drained = 0
        try:
            while drained < 50:  # 每轮最多取 50 条，避免饥饿
                msg = handler._q.get_nowait()
                await hub.publish(msg)
                drained += 1
        except thread_queue.Empty:
            pass
        except Exception:
            pass
        await asyncio.sleep(0.1 if drained == 0 else 0.02)


def install_log_forwarder() -> None:
    """安装日志转发器：在 root logger 上加 HubLogHandler，并启动后台转发任务"""
    handler = HubLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)

    hub = get_hub()
    asyncio.create_task(_forward_logs(handler, hub))
    logger.info("日志转发器已安装（Python logging → WebSocket）")
