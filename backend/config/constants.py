"""
常量定义模块
集中管理所有硬编码的默认值、预设和范围限制
"""

# API默认配置（不预设具体模型：不同厂商模型迭代快，固定默认值易过时。
# 默认留空，要求用户在设置页/环境变量里明确填写自己要用的模型名）
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_API_KEY = ""
DEFAULT_MODEL = ""  # 空 = 未配置模型，需用户在设置页填写

# 分析参数
DEFAULT_MAX_ARC_LENGTH = 1500      # 主线最大字数（长上下文建议800-1500）
DEFAULT_CONCURRENCY = 2            # 默认并行worker数
DEFAULT_BLOCK_SIZE = 1             # 默认每块章数（1=单章一块）

# Prompt构建参数（可配置）
DEFAULT_MAX_ARCS_IN_PROMPT = 30    # prompt中最多携带的主线条数
DEFAULT_MAX_SUMMARIES_IN_PROMPT = 5  # prompt中最多携带的摘要数
DEFAULT_TIMELINE_TRUNCATE = 200    # 时间线截断字符数
DEFAULT_MAX_CHARACTER_STATES_IN_PROMPT = 20
DEFAULT_MAX_WORLD_ITEMS_IN_PROMPT = 20
DEFAULT_MAX_FORESHADOW_ENTRIES_IN_PROMPT = 5
DEFAULT_MAX_FORESHADOW_CATALOG = 300  # 伏笔总表最大条目数（超出按 confidence 排序截断）
DEFAULT_BATCH_SUMMARY_MIN_WORDS = 2000  # 分卷摘要最低字数建议
DEFAULT_FINAL_REPORT_MIN_WORDS = 5000  # 最终报告最低字数建议

# Rolling Summary 参数（结构化 JSON 增量方案）
ROLLING_SUMMARY_MAX_TOKENS = None      # rolling 摘要输出 token 上限（None=不限制，让模型自然结束）
ROLLING_SUMMARY_MAX_RETRIES = 1       # rolling 调用失败后最多重试次数（总调用 = 1 + 此值）
ROLLING_SUMMARY_TEMPERATURE = 0.2     # rolling 调用温度（低温保证稳定）
ROLLING_FILE_NAME = "rolling_summary.json"  # rolling 持久化文件名

# 结构化滚动总结参数
ROLLING_EARLY_CHAPTERS = 100          # 首次生成结构化 JSON 的触发章数
ROLLING_MAX_MILESTONES = 20           # 全局里程碑上限（FIFO淘汰最旧的）
ROLLING_MAX_PARADIGMS = 5             # 范式层上限
ROLLING_MAX_MOMENTUM = 15             # 近期势头条目上限
ROLLING_MAX_CAUSAL_CHAINS = 5         # 因果链条目上限
ROLLING_MOMENTUM_WINDOW = 50          # 近期势头跨越多少章后触发归档
ROLLING_ARCHIVE_TRIGGER_COUNT = 10    # 条目数辅助触发归档（防单批爆满）

# 运行时参数
MAX_OUTPUT_TOKENS = 130000           # 单次LLM输出最大token数
DEFAULT_TIMEOUT = 300              # API超时时间（秒）
MAX_RETRIES = 5                    # 预留：APIConfig.max_retries 当前仅用于失败日志标注（record_failure 的 "尝试 n/m"），未接入重试循环；重试由 temperature_max_retries / backoff_max_retries 控制
DEFAULT_TEMPERATURE = 0.1          # 默认温度（温度退火起始值）
DEFAULT_TEMPERATURE_STEP = 0.05    # 温度退火每次降低量
TEMPERATURE_SEQUENCE = [0.1, 0.05, 0.0]  # 预留：APIConfig.temperature_sequence 当前未被任何代码读取；温度退火实际使用 temperature_step 算术递减，而非按本序列取值

# 重试配置
TEMPERATURE_MAX_RETRIES = 3        # 温度退火阶段最大重试次数
BACKOFF_MAX_RETRIES = 1            # 指数退避阶段默认重试次数

# 路径配置
WORK_DIR_NAME = "workspace"
BLOCKS_DIR_NAME = "blocks"
OUTPUT_DIR_NAME = "output"
CACHE_DIR_NAME = "cache"
CONFIG_FILE_NAME = "config.json"
KNOWLEDGE_FILE_NAME = "knowledge.json"

# 编码尝试列表
ENCODING_CANDIDATES = [
    'utf-8', 'gbk', 'gb2312', 'gb18030', 'big5',
    'utf-16', 'utf-8-sig', 'latin-1', 'cp1252',
    'shift_jis', 'euc-kr'
]

# 知识库限制
MAX_COMPRESSED_ARCS = 50        # 最多保留50条压缩主线
MAX_RECENT_SUMMARIES = 5        # 保留最近5章摘要
MAX_PACING_TRACKER = 100        # pacing_tracker 最大条目
MAX_FORESIGHT_NETWORK = 100     # foreshadowing_network 最大条目
MAX_WORLD_BUILDING = 200        # world_building 最大条目
MAX_VERIFIED_FACTS = 200        # verified_facts 最大条目
MAX_LONG_TERM_ARCS = 200        # long_term_arcs 最大条目
MAX_THEMATIC_ELEMENTS = 100     # thematic_elements 最大条目
MAX_THEMATIC_PER_CHAPTER = 5  # prompt中限制每章主题元素数

# 伏笔老化阈值：存活超过此章数的伏笔在 prompt 中标记为“长期未回收”
STALE_FORESHADOWING_THRESHOLD = 10


# GUI配置
DEFAULT_WINDOW_WIDTH = 1600
DEFAULT_WINDOW_HEIGHT = 900
DEFAULT_FONT_FAMILY = "Microsoft YaHei"
DEFAULT_FONT_SIZE = 12
DEFAULT_THEME = "light"  # light / dark

# 内存优化：Checkpoint 间隔（批数），每 N 批自动落盘，0=只在结束时落盘
DEFAULT_CHECKPOINT_INTERVAL = 5
