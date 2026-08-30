"""API 失败日志记录器（FailureLogger）
自 llm_client.py 拆出（2026-08-31 职责拆分）：专门记录 API 调用失败的详细信息，
进程级单例经 _get_failure_logger() 获取。

H15 修复（2026-08-26）：原实现用裸 open('a') 追加，无大小限制；24h-unlink
仅在 __init__ 触发一次，长跑不重启文件会一直增长。改用 RotatingFileHandler
（10MB × 3），轮转由 logging 体系自动管理，与 analyzer.log/crash.log 策略一致。
"""

import json
import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)


class FailureLogger:
    """失败日志记录器 - 专门记录API调用失败的详细信息（线程安全）"""

    def __init__(self, log_file: str = "api_failures.log"):
        self.log_file = Path(log_file)
        self.failures = []
        self._lock = threading.Lock()
        self._logger = self._build_logger()

    def _build_logger(self) -> logging.Logger:
        """构造带 RotatingFileHandler(10MB × 3) 的专用 logger，避免与别处句柄冲突。"""
        flog = logging.getLogger("api_failures")
        flog.setLevel(logging.INFO)
        flog.propagate = False
        if not any(
            getattr(h, "baseFilename", "") == str(self.log_file)
            for h in flog.handlers
        ):
            rfh = RotatingFileHandler(
                str(self.log_file), encoding="utf-8",
                maxBytes=10 * 1024 * 1024, backupCount=3,
            )
            # 简洁格式：每行一条 JSON 记录，便于外部脚本直接 grep/jq
            rfh.setFormatter(logging.Formatter("%(message)s"))
            flog.addHandler(rfh)
        return flog

    def record_failure(
        self,
        attempt_num: int,
        max_retries: int,
        temperature: float,
        error_type: str,
        error_message: str,
        wait_time: float = 0,
        messages_length: int = 0
    ):
        """记录一次失败（线程安全）"""
        failure = {
            "timestamp": datetime.now().isoformat(),
            "attempt": f"{attempt_num}/{max_retries}",
            "temperature": temperature,
            "error_type": error_type,
            "error_message": str(error_message)[:500],
            "wait_time_seconds": wait_time,
            "messages_length": messages_length
        }
        with self._lock:
            self.failures.append(failure)
            try:
                # 走 logging：单一文件流、自动轮转、单实例锁保护
                self._logger.info(json.dumps(failure, ensure_ascii=False))
            except Exception as e:
                logger.error(f"写入失败日志出错: {e}")

    def get_summary(self) -> dict:
        """获取失败统计摘要（线程安全）"""
        with self._lock:
            if not self.failures:
                return {"total_failures": 0}

            error_types = {}
            for f in self.failures:
                etype = f["error_type"]
                error_types[etype] = error_types.get(etype, 0) + 1

            return {
                "total_failures": len(self.failures),
                "error_types": error_types,
                "first_failure": self.failures[0]["timestamp"],
                "last_failure": self.failures[-1]["timestamp"]
            }

    def reset(self):
        """清空内存统计（队列模式下每本书开始前调用，避免跨书累积）"""
        with self._lock:
            self.failures.clear()


failure_logger = FailureLogger()


def _get_failure_logger():
    """获取 FailureLogger 单例（线程安全，内部已加锁）"""
    return failure_logger
