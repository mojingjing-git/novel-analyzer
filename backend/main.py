"""
后端入口

开发模式：
    python -m backend.main          # 起 uvicorn（无 reload，供 pywebview/生产用）
    uvicorn backend.app:app --reload --port 8000   # 开发热重载（推荐配合 vite）

发布模式：由 pywebview 入口调用 run_server()。
"""

import logging

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, reload: bool = False) -> None:
    # pythonw / PyInstaller 无控制台时，uvicorn 默认 log_config 会访问 sys.stdout.isatty()
    # 导致崩溃；传入 log_config=None 使用已配置的 logging
    uvicorn.run("backend.app:app", host=host, port=port, reload=reload, log_config=None)


if __name__ == "__main__":
    run_server()
