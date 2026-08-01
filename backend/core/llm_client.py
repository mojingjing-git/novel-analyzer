"""
LLM客户端封装（asyncio 版）
提供带重试机制和温度退火的 OpenAI 兼容 API 调用
（自旧项目 core/llm_client.py 移植：OpenAI→AsyncOpenAI，time.sleep→asyncio.sleep，
 行为契约保持不变：stop 检查在循环顶部、温度退火 max(0, temp-attempt*step)、
 退避 min(2^(retry+1), 60)、认证失败即停、验证失败注入格式修正 hint、failed_tokens 累加）
"""

import time
import json
import re
import asyncio
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional, List, Callable
from openai import AsyncOpenAI, APITimeoutError, APIError, AuthenticationError

from ..config.settings import APIConfig

logger = logging.getLogger(__name__)


class FailureLogger:
    """失败日志记录器 - 专门记录API调用失败的详细信息（线程安全）"""

    def __init__(self, log_file: str = "api_failures.log"):
        self.log_file = Path(log_file)
        self.failures = []
        self._lock = threading.Lock()
        self._check_reset()

    def _check_reset(self):
        """如果日志文件最后修改时间超过24小时，清空重新开始"""
        if self.log_file.exists():
            mtime = self.log_file.stat().st_mtime
            if time.time() - mtime > 86400:
                try:
                    self.log_file.unlink()
                    logger.info("api_failures.log 超过24小时，已重置")
                except OSError:
                    pass  # 文件被占用，跳过

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
            self._write_to_file(failure)

    def _write_to_file(self, failure: dict):
        """追加写入失败记录到文件（须在 _lock 内调用）"""
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(failure, ensure_ascii=False) + "\n")
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


class LLMClient:
    """OpenAI兼容API客户端（异步）"""

    def __init__(self, config: APIConfig):
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key if config.api_key else "empty"
        )
        self._stats_lock = threading.Lock()
        self._request_count = 0
        self._attempts = 0
        self._total_tokens = 0
        self._failed_tokens = 0
        self._cached_tokens = 0
        self._stop_requested = False  # 外部可设置，让进行中的重试链尽快退出

    def request_stop(self):
        """请求停止：让正在进行的 chat_with_retry 重试链在下一检查点退出"""
        self._stop_requested = True

    @staticmethod
    def _strip_thinking(content: str) -> str:
        """移除思考链标签（兼容各模型：<think>, <思考>, <reasoning> 等）"""
        if not content:
            return content
        # 处理 <think>...</think> 及各种变体（大小写不敏感，支持嵌套空白）
        content = re.sub(r'<think[^>]*>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<思考>.*?</思考>', '', content, flags=re.DOTALL)
        content = re.sub(r'<reasoning>.*?</reasoning>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<thought>.*?</thought>', '', content, flags=re.DOTALL | re.IGNORECASE)
        return content.strip()

    @staticmethod
    async def list_models(base_url: str, api_key: str) -> List[str]:
        """获取API可用的模型列表"""
        try:
            client = AsyncOpenAI(base_url=base_url, api_key=api_key if api_key else "empty")
            result = await client.models.list()
            # 标准端点：返回分页对象，模型列表在 .data 里（每个元素有 .id）
            raw = getattr(result, "data", None)
            if raw is None:
                # 部分非标准端点直接返回 list
                raw = result
            ids: List[str] = []
            for m in raw:
                # 兼容 Model 对象、dict、或直接就是 id 字符串 / (字段,值) tuple 的情况
                if isinstance(m, str):
                    ids.append(m)
                elif isinstance(m, tuple) and len(m) == 2 and m[0] == "data":
                    # pydantic BaseModel 默认的 __iter__ 会把字段迭代成 (名, 值)，
                    # 说明 result 没被解析成标准分页对象，此时从 .data 取值已在上面处理，
                    # 这里跳过这种顶层 tuple，避免误用。
                    continue
                elif hasattr(m, "id"):
                    ids.append(str(m.id))
                elif isinstance(m, dict) and "id" in m:
                    ids.append(str(m["id"]))
            return sorted(ids)
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            return []

    async def chat(
        self,
        messages: List[dict],
        temperature: float = 0.1,
        max_tokens: Optional[int] = None
    ) -> Tuple[bool, str, str, Tuple[int, int]]:
        if max_tokens is None:
            max_tokens = self.config.max_tokens

        # 结构化输出：仅对远程API生效，本地模型(localhost)跳过
        extra_kwargs = {}
        if self.config.json_mode != "default" and "localhost" not in self.config.base_url and "127.0.0.1" not in self.config.base_url:
            extra_kwargs["response_format"] = {"type": "json_object"}

        # 思考模式控制：thinking_mode 非空时注入 extra_body（与 response_format 共存不冲突）
        if self.config.thinking_mode:
            extra_kwargs["extra_body"] = dict(self.config.thinking_mode)

        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=self.config.timeout,
                    **extra_kwargs
                ),
                timeout=self.config.timeout + 30,  # 硬超时：比 SDK timeout 多 30s 余量
            )

            with self._stats_lock:
                self._request_count += 1
            prompt_tokens = 0
            completion_tokens = 0
            cached_tokens = 0
            if response.usage is not None:
                prompt_tokens = response.usage.prompt_tokens
                completion_tokens = response.usage.completion_tokens
                with self._stats_lock:
                    self._total_tokens += response.usage.total_tokens
                # 读取 KV cache 命中数（厂商扩展字段，无则 0）
                # OpenAI: usage.prompt_tokens_details.cached_tokens
                # DeepSeek: usage.prompt_cache_hit_tokens
                # 智谱/Qwen: 部分版本在 prompt_tokens_details
                ptd = getattr(response.usage, "prompt_tokens_details", None)
                if ptd is not None and getattr(ptd, "cached_tokens", None):
                    cached_tokens = ptd.cached_tokens
                cached_tokens = cached_tokens or getattr(response.usage, "prompt_cache_hit_tokens", 0) or 0
                with self._stats_lock:
                    self._cached_tokens += cached_tokens

            if not response.choices:
                error_msg = "API返回空choices列表（可能触发内容过滤）"
                logger.warning(error_msg)
                return False, "", error_msg, (0, 0)

            message = response.choices[0].message
            content = message.content or ""
            # mimo-v2.5 等推理模型：思考过程在 reasoning_content，直接丢弃
            reasoning = getattr(message, 'reasoning_content', None)
            if reasoning:
                logger.debug(f"丢弃推理模型 reasoning_content（{len(reasoning)}字符）")

            # 移除思考链标签（本地模型如Qwen3/Gemma4默认输出思考过程）
            original_len = len(content) if content else 0
            stripped = self._strip_thinking(content)
            # 如果 strip 后变空但原始内容非空（思考链被截断），保留原始内容
            if stripped:
                content = stripped
                if original_len > len(content):
                    logger.debug(f"已移除思考链: {original_len} -> {len(content)} 字符")
            elif content and original_len > 0:
                logger.debug(f"思考链 strip 后为空，保留原始内容（{original_len}字符）")
            cache_hint = f"，缓存命中{cached_tokens}" if cached_tokens else ""
            logger.info(f"API请求成功，使用{response.usage.total_tokens if response.usage else 0} tokens (输入{prompt_tokens}+输出{completion_tokens}{cache_hint})")

            return True, content, "", (prompt_tokens, completion_tokens)

        except asyncio.TimeoutError:
            error_msg = f"请求硬超时 (timeout={self.config.timeout + 30}s): API 无响应"
            logger.warning(error_msg)
            _get_failure_logger().record_failure(
                attempt_num=0,
                max_retries=self.config.max_retries,
                temperature=temperature,
                error_type="HardTimeout",
                error_message=error_msg,
                messages_length=len(str(messages))
            )
            return False, "", error_msg, (0, 0)

        except AuthenticationError as e:
            error_msg = f"认证失败，请检查API Key: {str(e)}"
            logger.error(error_msg)

            _get_failure_logger().record_failure(
                attempt_num=0,
                max_retries=self.config.max_retries,
                temperature=temperature,
                error_type="AuthenticationError",
                error_message=error_msg,
                messages_length=len(str(messages))
            )
            return False, "", error_msg, (0, 0)

        except APITimeoutError as e:
            error_msg = f"请求超时 (timeout={self.config.timeout}s): {str(e)}"
            logger.warning(error_msg)

            _get_failure_logger().record_failure(
                attempt_num=0,
                max_retries=self.config.max_retries,
                temperature=temperature,
                error_type="TimeoutError",
                error_message=error_msg,
                messages_length=len(str(messages))
            )
            return False, "", error_msg, (0, 0)

        except APIError as e:
            status_code = getattr(e, 'status_code', None)
            error_msg = f"API错误 (HTTP {status_code}): {str(e)}"

            if status_code == 429:
                logger.warning(f"触发速率限制(429)，建议增加重试等待时间")

            logger.error(error_msg)
            _get_failure_logger().record_failure(
                attempt_num=0,
                max_retries=self.config.max_retries,
                temperature=temperature,
                error_type=f"APIError_HTTP{status_code}",
                error_message=error_msg,
                messages_length=len(str(messages))
            )
            return False, "", error_msg, (0, 0)

        except Exception as e:
            error_msg = f"未知错误: {type(e).__name__}: {str(e)}"
            logger.error(error_msg, exc_info=True)

            _get_failure_logger().record_failure(
                attempt_num=0,
                max_retries=self.config.max_retries,
                temperature=temperature,
                error_type=type(e).__name__,
                error_message=error_msg,
                messages_length=len(str(messages))
            )
            return False, "", error_msg, (0, 0)

    async def chat_with_retry(
        self,
        messages: List[dict],
        max_tokens: Optional[int] = None,
        validate_response: Optional[Callable[[str], Tuple[bool, str]]] = None,
        retry_messages_builder: Optional[Callable[[str, List[dict]], List[dict]]] = None
    ) -> Tuple[bool, str, str, Tuple[int, int], dict]:
        last_error = ""
        total_attempts = 0
        # 本次调用的失败 token 累计（并发安全：不再依赖客户端全局计数做差分归因）
        call_failed_tokens = 0
        messages_length = len(str(messages))
        # 跟踪最后一次 API 成功调用消耗的 tokens（即使 JSON 验证失败，API 已扣费）
        last_consumed_tokens: Tuple[int, int] = (0, 0)

        logger.info("=" * 60)
        logger.info(f"开始API调用 - Model: {self.config.model}")
        logger.info(f"配置: temperature={self.config.temperature}, temperature_step={self.config.temperature_step}, "
                   f"temperature_max_retries={self.config.temperature_max_retries}, "
                   f"backoff_max_retries={self.config.backoff_max_retries}")
        if validate_response:
            logger.info(f"启用响应验证回调")
        logger.info("=" * 60)

        # 温度退火重试
        for attempt in range(self.config.temperature_max_retries):
            if self._stop_requested:
                logger.info("⛔ 检测到停止请求，终止重试链")
                return False, "", "用户请求停止", last_consumed_tokens, {"attempts": total_attempts, "failed_tokens": call_failed_tokens}
            total_attempts += 1
            with self._stats_lock:
                self._attempts += 1

            temp = max(0.0, self.config.temperature - attempt * self.config.temperature_step)

            logger.info(f"\n[温度退火] 尝试 {total_attempts}，"
                       f"temperature={temp} (第{attempt + 1}/{self.config.temperature_max_retries}次)")

            success, content, error, tokens = await self.chat(
                messages, temperature=temp, max_tokens=max_tokens
            )

            if success:
                if validate_response:
                    # 在线程中运行验证，避免 json5/ast.literal_eval 等同步解析器阻塞事件循环
                    try:
                        is_valid, validation_error = await asyncio.wait_for(
                            asyncio.to_thread(validate_response, content),
                            timeout=30
                        )
                    except asyncio.TimeoutError:
                        logger.warning("❌ 响应验证超时（30秒），视为验证失败")
                        is_valid, validation_error = False, "验证超时（30秒）"
                    except Exception as e:
                        logger.warning(f"❌ 响应验证异常: {e}")
                        is_valid, validation_error = False, f"验证异常: {e}"
                    if not is_valid:
                        logger.warning(f"❌ 响应验证失败: {validation_error}")
                        logger.info(f"[DEBUG] LLM响应(全文): {repr(content)}")
                        # 累加失败 tokens（API 已消耗，但响应无效）
                        if isinstance(tokens, tuple) and len(tokens) == 2:
                            with self._stats_lock:
                                self._failed_tokens += tokens[0] + tokens[1]
                            call_failed_tokens += tokens[0] + tokens[1]
                            last_consumed_tokens = tokens  # 保留以供最终返回
                        last_error = f"响应验证失败: {validation_error}"
                        # 构建带格式修正提示的messages用于重试
                        if retry_messages_builder:
                            messages = retry_messages_builder(validation_error, messages)
                        _get_failure_logger().record_failure(
                            attempt_num=total_attempts,
                            max_retries=self.config.temperature_max_retries + self.config.backoff_max_retries,
                            temperature=temp,
                            error_type="ValidationFailed",
                            error_message=last_error,
                            wait_time=0,
                            messages_length=messages_length
                        )
                        if attempt < self.config.temperature_max_retries - 1:
                            wait_time = 3 * (attempt + 1)
                            logger.info(f"⏳ 等待{wait_time}秒后进行下一次温度尝试...")
                            await asyncio.sleep(wait_time)
                        continue

                logger.info(f"✅ API请求成功（第{total_attempts}次尝试）")
                logger.info(f"使用Tokens: {tokens}")
                logger.info("=" * 60)
                return True, content, "", tokens, {"attempts": total_attempts, "failed_tokens": call_failed_tokens}

            last_error = error
            logger.warning(f"❌ 尝试失败: {error}")

            # 即使 API 调用失败，如果有成功的 tokens（如超时但部分计费），保留
            if isinstance(tokens, tuple) and len(tokens) == 2 and (tokens[0] > 0 or tokens[1] > 0):
                last_consumed_tokens = tokens

            _get_failure_logger().record_failure(
                attempt_num=total_attempts,
                max_retries=self.config.temperature_max_retries + self.config.backoff_max_retries,
                temperature=temp,
                error_type="RetryNeeded",
                error_message=error,
                wait_time=0,
                messages_length=messages_length
            )

            if "认证失败" in error or "401" in error:
                logger.error("⛔ 认证错误，停止重试")
                return False, "", error, last_consumed_tokens, {"attempts": total_attempts, "failed_tokens": call_failed_tokens}

            if attempt < self.config.temperature_max_retries - 1:
                wait_time = 3 * (attempt + 1)
                logger.info(f"⏳ 等待{wait_time}秒后进行下一次温度尝试...")
                await asyncio.sleep(wait_time)

        # 指数退避重试
        final_temp = max(0.0, self.config.temperature - (self.config.temperature_max_retries - 1) * self.config.temperature_step)
        for retry_num in range(self.config.backoff_max_retries):
            if self._stop_requested:
                logger.info("⛔ 检测到停止请求，终止重试链")
                return False, "", "用户请求停止", last_consumed_tokens, {"attempts": total_attempts, "failed_tokens": call_failed_tokens}
            total_attempts += 1
            with self._stats_lock:
                self._attempts += 1

            wait_time = min(2 ** (retry_num + 1), 60)

            logger.info(f"\n[指数退避] 尝试 {total_attempts}，"
                       f"等待{wait_time}秒，temperature={final_temp} "
                       f"(第{retry_num + 1}/{self.config.backoff_max_retries}次)")
            await asyncio.sleep(wait_time)

            success, content, error, tokens = await self.chat(
                messages,
                temperature=final_temp,
                max_tokens=max_tokens
            )

            if success:
                if validate_response:
                    # 在线程中运行验证，避免 json5/ast.literal_eval 等同步解析器阻塞事件循环
                    try:
                        is_valid, validation_error = await asyncio.wait_for(
                            asyncio.to_thread(validate_response, content),
                            timeout=30
                        )
                    except asyncio.TimeoutError:
                        logger.warning("❌ 响应验证超时（30秒），视为验证失败")
                        is_valid, validation_error = False, "验证超时（30秒）"
                    except Exception as e:
                        logger.warning(f"❌ 响应验证异常: {e}")
                        is_valid, validation_error = False, f"验证异常: {e}"
                    if not is_valid:
                        logger.warning(f"❌ 响应验证失败: {validation_error}")
                        logger.info(f"[DEBUG] LLM响应(全文): {repr(content)}")
                        # 累加失败 tokens（API 已消耗，但响应无效）
                        if isinstance(tokens, tuple) and len(tokens) == 2:
                            with self._stats_lock:
                                self._failed_tokens += tokens[0] + tokens[1]
                            call_failed_tokens += tokens[0] + tokens[1]
                            last_consumed_tokens = tokens
                        last_error = f"响应验证失败: {validation_error}"
                        # 构建带格式修正提示的messages用于重试
                        if retry_messages_builder:
                            messages = retry_messages_builder(validation_error, messages)
                        _get_failure_logger().record_failure(
                            attempt_num=total_attempts,
                            max_retries=self.config.temperature_max_retries + self.config.backoff_max_retries,
                            temperature=final_temp,
                            error_type="ValidationFailed",
                            error_message=last_error,
                            wait_time=wait_time,
                            messages_length=messages_length
                        )
                        continue

                logger.info(f"✅ API请求成功（第{total_attempts}次尝试）")
                logger.info(f"使用Tokens: {tokens}")
                logger.info("=" * 60)
                return True, content, "", tokens, {"attempts": total_attempts, "failed_tokens": call_failed_tokens}

            last_error = error
            logger.warning(f"❌ 重试失败: {error}")

            # 保留 API 调用消耗的 tokens
            if isinstance(tokens, tuple) and len(tokens) == 2 and (tokens[0] > 0 or tokens[1] > 0):
                last_consumed_tokens = tokens

            _get_failure_logger().record_failure(
                attempt_num=total_attempts,
                max_retries=self.config.temperature_max_retries + self.config.backoff_max_retries,
                temperature=final_temp,
                error_type="ExponentialBackoff",
                error_message=error,
                wait_time=wait_time,
                messages_length=messages_length
            )

            if "认证失败" in error or "401" in error:
                logger.error("⛔ 认证错误，停止重试")
                return False, "", error, last_consumed_tokens, {"attempts": total_attempts, "failed_tokens": call_failed_tokens}

        final_msg = f"所有重试均失败（温度退火{self.config.temperature_max_retries}次 + " \
                   f"指数退避{self.config.backoff_max_retries}次 = 共{total_attempts}次）。" \
                   f"最后错误: {last_error}"
        logger.error("\n" + "=" * 60)
        logger.error(f"⛔ API调用最终失败")
        logger.error(f"总尝试次数: {total_attempts}")
        logger.error(f"最后错误: {last_error}")

        summary = _get_failure_logger().get_summary()
        logger.error(f"失败统计: {summary}")
        logger.error("=" * 60)

        return False, "", final_msg, last_consumed_tokens, {"attempts": total_attempts, "failed_tokens": call_failed_tokens}

    def get_stats(self) -> dict:
        return {
            "total_requests": self._request_count,
            "total_attempts": self._attempts,
            "total_tokens": self._total_tokens,
            "failed_tokens": self._failed_tokens,
            "cached_tokens": self._cached_tokens,
            "failure_log_summary": _get_failure_logger().get_summary()
        }
