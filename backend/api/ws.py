"""
WebSocket 进度推送端点

/ws/progress — 客户端连接后持续接收 ProgressHub 广播的进度消息。
心跳：服务端每 25 秒发一次 {"type": "ping"}，防止代理/浏览器断连。
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.progress_hub import get_hub

logger = logging.getLogger(__name__)

router = APIRouter()

_HEARTBEAT_INTERVAL = 25.0

# 允许连接进度 WebSocket 的来源白名单（本地桌面应用）。
# 缺失/空 Origin（pywebview 本地上下文、同源 file://）放行；其余仅允许 localhost / 127.0.0.1，
# 阻断来自远程网页的跨站 WebSocket 连接。
_ALLOWED_ORIGIN_PREFIXES = (
    "http://localhost",
    "http://127.0.0.1",
    "https://localhost",
    "https://127.0.0.1",
)


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    origin = origin.strip().lower()
    return any(origin == p or origin.startswith(p + ":") for p in _ALLOWED_ORIGIN_PREFIXES)


@router.websocket("/ws/progress")
async def ws_progress(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if not _origin_allowed(origin):
        logger.warning("WS 连接被拒绝：非法 Origin=%r", origin)
        await websocket.close(code=1008)
        return
    await websocket.accept()
    hub = get_hub()
    queue = await hub.subscribe()
    try:
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                message = {"type": "ping"}
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS 连接异常关闭: %s", e)
    finally:
        await hub.unsubscribe(queue)
