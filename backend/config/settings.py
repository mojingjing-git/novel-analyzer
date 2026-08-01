"""
配置管理模块
负责加载、保存和验证应用配置
（自旧项目 config/settings.py 移植，新增 workspace_dir + max_* 知识库限制字段）
"""

import json
import os
import dataclasses
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from .constants import (
    DEFAULT_BASE_URL, DEFAULT_API_KEY, DEFAULT_MODEL,
    DEFAULT_MAX_ARC_LENGTH,
    DEFAULT_CONCURRENCY, DEFAULT_BLOCK_SIZE,
    DEFAULT_MAX_ARCS_IN_PROMPT,
    DEFAULT_MAX_SUMMARIES_IN_PROMPT, DEFAULT_TIMELINE_TRUNCATE,
    DEFAULT_MAX_CHARACTER_STATES_IN_PROMPT,
    DEFAULT_MAX_WORLD_ITEMS_IN_PROMPT,
    DEFAULT_MAX_FORESHADOW_ENTRIES_IN_PROMPT,
    DEFAULT_MAX_FORESHADOW_CATALOG,
    DEFAULT_BATCH_SUMMARY_MIN_WORDS,
    DEFAULT_FINAL_REPORT_MIN_WORDS,
    MAX_OUTPUT_TOKENS, DEFAULT_TIMEOUT, MAX_RETRIES,
    DEFAULT_TEMPERATURE, DEFAULT_TEMPERATURE_STEP,
    TEMPERATURE_SEQUENCE, TEMPERATURE_MAX_RETRIES, BACKOFF_MAX_RETRIES,
    ENCODING_CANDIDATES,
    CONFIG_FILE_NAME, KNOWLEDGE_FILE_NAME,
    DEFAULT_CHECKPOINT_INTERVAL,
    ROLLING_EARLY_CHAPTERS,
    ROLLING_MAX_MILESTONES, ROLLING_MAX_MOMENTUM,
    ROLLING_MOMENTUM_WINDOW, ROLLING_ARCHIVE_TRIGGER_COUNT,
    MAX_COMPRESSED_ARCS, MAX_RECENT_SUMMARIES,
    MAX_PACING_TRACKER, MAX_FORESIGHT_NETWORK,
    MAX_WORLD_BUILDING, MAX_VERIFIED_FACTS,
    MAX_LONG_TERM_ARCS, MAX_THEMATIC_ELEMENTS,
)


def _filter_fields(cls, data: dict) -> dict:
    """过滤掉 dataclass 不认识的字段，防止旧配置含未知字段时崩溃"""
    valid = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in valid}


@dataclass
class APIConfig:
    """API配置"""
    base_url: str = DEFAULT_BASE_URL
    api_key: str = DEFAULT_API_KEY
    model: str = DEFAULT_MODEL
    max_tokens: int = MAX_OUTPUT_TOKENS
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = MAX_RETRIES
    json_mode: str = "default"  # default/qwen/deepseek/glm47
    temperature: float = DEFAULT_TEMPERATURE
    temperature_step: float = DEFAULT_TEMPERATURE_STEP
    temperature_sequence: List[float] = field(default_factory=lambda: TEMPERATURE_SEQUENCE.copy())
    temperature_max_retries: int = TEMPERATURE_MAX_RETRIES
    backoff_max_retries: int = BACKOFF_MAX_RETRIES
    thinking_mode: dict = field(default_factory=dict)  # {}=自动过滤, {"thinking":{"type":"disabled"}}=mimo/GLM, {"enable_thinking":false}=DeepSeek/Qwen


@dataclass
class AnalysisConfig:
    """分析配置"""
    max_arc_length: int = DEFAULT_MAX_ARC_LENGTH
    concurrency: int = DEFAULT_CONCURRENCY
    block_size: int = DEFAULT_BLOCK_SIZE
    encoding_priority: List[str] = field(default_factory=lambda: ENCODING_CANDIDATES.copy())
    max_arcs_in_prompt: int = DEFAULT_MAX_ARCS_IN_PROMPT
    max_summaries_in_prompt: int = DEFAULT_MAX_SUMMARIES_IN_PROMPT
    timeline_truncate: int = DEFAULT_TIMELINE_TRUNCATE
    max_character_states: int = DEFAULT_MAX_CHARACTER_STATES_IN_PROMPT
    max_world_items: int = DEFAULT_MAX_WORLD_ITEMS_IN_PROMPT
    max_foreshadow_entries: int = DEFAULT_MAX_FORESHADOW_ENTRIES_IN_PROMPT
    max_foreshadow_catalog: int = DEFAULT_MAX_FORESHADOW_CATALOG
    batch_summary_min_words: int = DEFAULT_BATCH_SUMMARY_MIN_WORDS
    final_report_min_words: int = DEFAULT_FINAL_REPORT_MIN_WORDS
    rolling_early_chapters: int = ROLLING_EARLY_CHAPTERS
    rolling_max_milestones: int = ROLLING_MAX_MILESTONES
    rolling_max_momentum: int = ROLLING_MAX_MOMENTUM
    rolling_momentum_window: int = ROLLING_MOMENTUM_WINDOW
    rolling_archive_trigger_count: int = ROLLING_ARCHIVE_TRIGGER_COUNT
    checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL  # 每 N 批自动落盘，0=只在结束时落盘
    auto_archive: bool = False  # 队列分析完成后自动归档整个小说目录到 分析结果/
    # 知识库限制
    max_compressed_arcs: int = MAX_COMPRESSED_ARCS
    max_recent_summaries: int = MAX_RECENT_SUMMARIES
    max_pacing_tracker: int = MAX_PACING_TRACKER
    max_foreshadow_network: int = MAX_FORESIGHT_NETWORK
    max_world_building: int = MAX_WORLD_BUILDING
    max_verified_facts: int = MAX_VERIFIED_FACTS
    max_long_term_arcs: int = MAX_LONG_TERM_ARCS
    max_thematic_elements: int = MAX_THEMATIC_ELEMENTS


@dataclass
class GUIConfig:
    """GUI配置（Web 版保留兼容旧 config.json，字段沿用）"""
    window_width: int = 1600
    window_height: int = 900
    font_family: str = "Microsoft YaHei"
    font_size: int = 12
    quit_on_close: bool = True
    theme: str = "light"


@dataclass
class AppConfig:
    """应用总配置"""
    api: APIConfig = field(default_factory=APIConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    gui: GUIConfig = field(default_factory=GUIConfig)

    # 运行时路径
    working_directory: Optional[str] = None
    knowledge_file: str = KNOWLEDGE_FILE_NAME
    workspace_dir: str = "workspace"  # 工作区目录名（相对于项目根）

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'AppConfig':
        """从字典创建配置"""
        api_data = data.get('api', {})
        analysis_data = data.get('analysis', {})
        gui_data = data.get('gui', {})

        # 兼容旧配置：移除已废弃的字段
        analysis_data.pop('compress_every', None)
        analysis_data.pop('rolling_summary_target_chars', None)
        analysis_data.pop('rolling_early_target_chars', None)
        analysis_data.pop('rolling_recent_target_chars', None)

        # 兼容旧配置：thinking_mode 必须是 dict，字符串则清空回默认值
        tm = api_data.get('thinking_mode')
        if tm is not None and not isinstance(tm, dict):
            api_data.pop('thinking_mode', None)

        # 兼容旧配置：model 为空字符串/缺省时回落到默认模型（DEFAULT_MODEL，当前为空
        # = 未配置）。空模型名会在 API 预检查阶段被拦出，要求用户在设置页填写。
        if not str(api_data.get('model', '')).strip():
            api_data.pop('model', None)

        return cls(
            api=APIConfig(**_filter_fields(APIConfig, api_data)),
            analysis=AnalysisConfig(**_filter_fields(AnalysisConfig, analysis_data)),
            gui=GUIConfig(**_filter_fields(GUIConfig, gui_data)),
            working_directory=data.get('working_directory'),
            knowledge_file=data.get('knowledge_file', KNOWLEDGE_FILE_NAME),
            workspace_dir=data.get('workspace_dir', 'workspace'),
        )


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = Path(CONFIG_FILE_NAME)
        self.config_path = config_path
        self.config = AppConfig()

    def load(self) -> AppConfig:
        """从文件加载配置"""
        if not self.config_path.exists():
            return self.config

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.config = AppConfig.from_dict(data)
        except Exception as e:
            print(f"加载配置失败: {e}，使用默认配置")
            return self.config

        # 安全：API Key 优先从环境变量读取，避免明文硬编码落在 config.json（曾明文提交密钥）。
        # 设置 LLM_API_KEY 或 MINIMAX_API_KEY 即可覆盖；未设置且 config 中也为空时给出明确警告。
        env_key = os.environ.get("LLM_API_KEY") or os.environ.get("MINIMAX_API_KEY")
        if env_key:
            self.config.api.api_key = env_key
        elif not self.config.api.api_key:
            print("⚠️ 未配置 API Key：请设置环境变量 LLM_API_KEY（推荐），"
                  "或在 config.json 的 api.api_key 中填入（注意该文件不应提交含密钥的版本）。")

        # 模型未配置提示（与 API Key 提示并列，避免带着空模型名开始分析）
        if not str(self.config.api.model or '').strip():
            print("⚠️ 未配置模型（api.model 为空）：请在设置页填写你要使用的模型名，"
                  "否则 API 预检查与分析都会失败。")

        return self.config

    def save(self, config: Optional[AppConfig] = None) -> bool:
        """保存配置到文件"""
        if config is not None:
            self.config = config

        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False

    def update(self, **kwargs):
        """更新配置项"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
