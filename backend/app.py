"""
FastAPI 应用工厂

开发模式：uvicorn backend.app:app --reload （配合 vite dev 前端）
发布模式：托管 frontend/dist 静态文件，pywebview 指向本服务
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api import (
    routes_analysis,
    routes_books,
    routes_prompt,
    routes_settings,
    routes_splitter,
    routes_summary,
    routes_viz,
    routes_workspace,
    routes_aggregate,
    routes_foreshadow,
    ws,
)
from backend.progress_hub import get_hub, install_log_forwarder

logger = logging.getLogger(__name__)

# 项目根目录（novel_analyzer/）；兼容 PyInstaller 打包后的 _MEIPASS
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    # 打包后日志写到用户目录（持久化，避免 _MEIPASS 临时目录被删）
    _log_dir = Path(os.environ.get("APPDATA", Path.home())) / "NovelAnalyzer"
    _log_dir.mkdir(parents=True, exist_ok=True)
    LOG_FILE = _log_dir / "analyzer.log"
else:
    # NOVEL_ROOT 允许从“本地副本”启动进程（WebView2 需本地路径），
    # 同时让数据/日志仍落在共享盘真实项目根。
    _novel_root = os.environ.get("NOVEL_ROOT")
    PROJECT_ROOT = Path(_novel_root).resolve() if _novel_root else Path(__file__).resolve().parent.parent
    LOG_FILE = PROJECT_ROOT / "analyzer.log"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

# 配置日志文件轮转（10MB x 3）
_file_handler = RotatingFileHandler(
    str(LOG_FILE), encoding="utf-8", maxBytes=10 * 1024 * 1024, backupCount=3
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_file_handler)


def create_app() -> FastAPI:
    # P2-12：@app.on_event("startup") 已被 FastAPI 弃用（DeprecationWarning），
    # 迁移到 lifespan 上下文管理器（行为等价）。
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        install_log_forwarder()  # 内部使用 asyncio.create_task，需在事件循环中调用

        # 把未检索的 asyncio 任务异常写入日志（默认只打到 stderr，桌面端会丢失）
        try:
            loop = asyncio.get_running_loop()
            _orig_handler = loop.get_exception_handler()

            def _exc_handler(loop, context):
                msg = context.get("message", "Unhandled asyncio exception")
                exc = context.get("exception")
                logger.error(f"[asyncio] {msg}", exc_info=exc)
                if _orig_handler:
                    _orig_handler(loop, context)

            loop.set_exception_handler(_exc_handler)
        except Exception as e:
            logger.debug(f"设置 asyncio 异常处理器失败: {e}")
        yield

    app = FastAPI(title="Novel Analyzer Web", version="0.1.0", lifespan=lifespan)

    # 开发期前端跑在 vite dev server（不同端口），需要 CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 桌面应用缓存策略：前端是本地实时构建产物，必须禁用浏览器缓存。
    # 否则 WebView2 会按 Starlette 默认 Cache-Control: max-age=3600 缓存 index.html，
    # 重建 dist 后用户仍看到旧效果（"改了但没生效"的典型假象）。
    @app.middleware("http")
    async def disable_frontend_cache(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            # Starlette 的 MutableHeaders 没有 .pop() 方法，用 del 守卫式删除
            if "etag" in response.headers:
                del response.headers["etag"]
            if "last-modified" in response.headers:
                del response.headers["last-modified"]
        return response

    # WebSocket 进度通道
    app.include_router(ws.router)

    # REST 路由
    app.include_router(routes_settings.router)
    app.include_router(routes_analysis.router)
    app.include_router(routes_books.router)
    app.include_router(routes_summary.router)
    app.include_router(routes_viz.router)
    app.include_router(routes_splitter.router)
    app.include_router(routes_workspace.router)
    app.include_router(routes_prompt.router)
    app.include_router(routes_aggregate.router)
    app.include_router(routes_foreshadow.router)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "app": "novel-analyzer-web"}

    @app.post("/api/demo/publish")
    async def demo_publish():
        """M0 回环验证：同步发布一串演示进度消息，WS 客户端应能收到"""
        hub = get_hub()
        await hub.state_change("running", "demo 开始")
        for i in range(1, 6):
            await hub.log(f"demo 日志第 {i} 条")
            await hub.progress(i, 5, eta=f"{5 - i} 秒")
        await hub.state_change("done", "demo 结束")
        return {"ok": True, "published": 12}

    # 发布模式：托管前端构建产物（必须放在最后，避免吞掉 /api 路由）
    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
        logger.info("已托管前端静态文件: %s", FRONTEND_DIST)

    return app


app = create_app()
