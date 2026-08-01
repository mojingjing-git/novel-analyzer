"""
分析引擎（asyncio 版）
协调LLM调用、结果解析和知识库更新的核心逻辑
（自旧项目 core/analyzer.py 移植：analyze_chapter 改 async，
内部 await llm_client.chat_with_retry；验证/解析逻辑零改动）
"""

import logging
from typing import Optional, Tuple

from ..config.settings import AppConfig
from ..models.analysis_result import AnalysisResult
from ..models.knowledge import KnowledgeBase
from .llm_client import LLMClient
from .prompt_builder import PromptBuilder
from ..utils.json_utils import extract_json_from_text, safe_parse_json

logger = logging.getLogger(__name__)


class NovelAnalyzer:
    """小说分析引擎"""

    def __init__(self, config: AppConfig):
        """
        初始化分析引擎

        Args:
            config: 应用配置
        """
        self.config = config
        self.llm_client = LLMClient(config.api)
        self.prompt_builder = PromptBuilder(
            max_arc_length=config.analysis.max_arc_length,
            max_arcs=config.analysis.max_arcs_in_prompt,
            max_summaries=config.analysis.max_summaries_in_prompt,
            timeline_truncate=config.analysis.timeline_truncate,
            max_character_states=config.analysis.max_character_states,
            max_world_items=config.analysis.max_world_items,
            max_foreshadow_entries=config.analysis.max_foreshadow_entries,
        )
        self._is_stopped = False

    def stop(self):
        """停止分析"""
        self._is_stopped = True
        # 联动 LLMClient：让进行中的 chat_with_retry 重试链在下一检查点退出
        self.llm_client.request_stop()
        logger.info("分析已请求停止")

    def get_token_stats(self) -> dict:
        """获取Token使用统计"""
        return self.llm_client.get_stats()

    async def analyze_chapter(
        self,
        chapter_number: int,
        chapter_content: str,
        knowledge_base: KnowledgeBase,
        block_size: int = 1
    ) -> Tuple[Optional[AnalysisResult], Tuple[int, int]]:
        """
        分析单个章节（并发安全版本）

        Args:
            chapter_number: 章节号
            chapter_content: 章节内容
            knowledge_base: 当前知识库（调用方传入，不在此方法内读写共享KB）
            block_size: 本块合并的章节数（写入 result 用于断点续跑时检测 block_size 变更）

        Returns:
            (分析结果, 本章消耗token数)，失败返回 (None, (0, 0))
        """
        if self._is_stopped:
            logger.info("分析已被停止")
            return None, (0, 0)

        try:
            # 1. 使用传入的KB构建提示词
            messages = self.prompt_builder.build_messages(
                chapter_content, chapter_number, knowledge_base
            )

            logger.info(f"开始分析第{chapter_number}章...")

            # 定义JSON验证回调
            parsed_cache = [None]

            def validate_json_response(response: str) -> Tuple[bool, str]:
                """验证LLM响应是否能成功解析为JSON，并检查必须字段"""
                logger.debug(f"开始验证响应（长度={len(response)}字符）")
                json_str = extract_json_from_text(response)
                if json_str is None:
                    return False, "无法从响应中提取JSON"
                data = safe_parse_json(json_str)
                if data is None:
                    return False, "JSON解析失败"
                # 检查必须字段是否存在且非空
                required_fields = ["core_events", "cross_block", "long_context_insights"]
                missing = [k for k in required_fields if k not in data]
                if missing:
                    return False, f"JSON缺少必须字段: {missing}"
                # cross_block.summary 是最重要的字段，必须非空
                cb = data.get("cross_block", {})
                if not isinstance(cb, dict) or not cb.get("summary"):
                    return False, "cross_block.summary 为空或类型错误"
                # core_events 必须是列表
                ce = data.get("core_events", [])
                if not isinstance(ce, list):
                    return False, f"core_events 应为列表，实际为 {type(ce).__name__}"
                parsed_cache[0] = data
                logger.debug(f"响应验证通过（{len(data)}个顶层字段）")
                return True, ""

            # 定义重试时的格式修正提示构建器
            def build_retry_messages(validation_error: str, current_messages: list) -> list:
                """在user message末尾追加格式修正提示"""
                hint = f"\n\n[系统提示] 上次输出格式有误（{validation_error}），请严格按JSON格式输出，确保包含core_events、cross_block、long_context_insights等必须字段。"
                retry_msgs = [dict(m) for m in current_messages]
                for msg in reversed(retry_msgs):
                    if msg.get("role") == "user":
                        msg["content"] = msg["content"] + hint
                        break
                return retry_msgs

            # 2. 调用LLM（带重试和温度退火，包含JSON解析验证）
            success, response, error, token_counts = await self.llm_client.chat_with_retry(
                messages,
                max_tokens=self.config.api.max_tokens,
                validate_response=validate_json_response,
                retry_messages_builder=build_retry_messages
            )

            if not success:
                logger.error(f"第{chapter_number}章分析失败: {error}")
                # 保留实际消耗的 tokens（API 已扣费，不能归零）
                actual_tokens = token_counts if isinstance(token_counts, tuple) else (0, 0)
                return None, actual_tokens

            in_tok, out_tok = token_counts if isinstance(token_counts, tuple) else (0, token_counts)
            tokens = in_tok + out_tok
            logger.info(f"第{chapter_number}章API调用成功，使用{tokens} tokens (输入{in_tok}+输出{out_tok})")

            # 3. 解析响应（复用 validate 阶段的解析结果）
            result = self._parse_response(response, chapter_number, parsed_cache[0])
            if result is None:
                logger.error(f"第{chapter_number}章JSON解析失败，LLM响应前800字符:\n{response[:800]}")
                return None, (in_tok, out_tok)

            result.raw_response = response
            result.block_size = block_size

            # 4. 文件写入由 MemoryState.flush_to_disk() 统一处理
            logger.debug(f"第{chapter_number}章 result 已在内存中")

            logger.info(f"第{chapter_number}章分析完成")
            return result, (in_tok, out_tok)

        except Exception as e:
            logger.error(f"第{chapter_number}章分析异常: {e}", exc_info=True)
            # 尽量保留已消耗的 tokens
            return None, (0, 0)

    def _parse_response(self, response: str, chapter_number: int,
                         pre_parsed: Optional[dict] = None) -> Optional[AnalysisResult]:
        """
        解析LLM响应为AnalysisResult

        Args:
            response: LLM响应文本
            chapter_number: 章节号
            pre_parsed: 如果已经在 validate 阶段解析过，直接传入避免重复解析

        Returns:
            分析结果，失败返回None
        """
        if pre_parsed is not None:
            data = pre_parsed
        else:
            json_str = extract_json_from_text(response)
            if json_str is None:
                logger.error("无法从响应中提取JSON")
                logger.debug(f"原始响应前500字符: {response[:500]}")
                return None

            data = safe_parse_json(json_str)
            if data is None:
                logger.error("JSON解析失败")
                logger.debug(f"提取的JSON前500字符: {json_str[:500]}")
                return None

        try:
            # 转换为AnalysisResult
            result = AnalysisResult.from_dict(data)
            result.chapter_number = chapter_number
            return result

        except Exception as e:
            logger.error(f"构建AnalysisResult失败: {e}")
            return None
