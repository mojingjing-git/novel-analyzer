"""
最终总结服务（asyncio 版）
分批调用 LLM 对分析结果进行全书脉络总结，集成伏笔账本管理。
（从旧项目 workers/final_summary_worker.py 移植：QThread→async 类，
 Qt 信号→on_progress/on_token_stats 回调，ThreadPoolExecutor→asyncio.Semaphore+Task，
 threading.Lock→asyncio.Lock；prompt 与账本逻辑逐字保持不变）
"""

import json
import re
import asyncio
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable

from backend.config.settings import AppConfig
from backend.core.llm_client import LLMClient
from backend.core.style_analyzer import extract_style_profile
from backend.utils.json_utils import safe_load_json, extract_json_from_text, safe_parse_json
from backend.utils.text_utils import deduplicate_foreshadows
from backend.utils.foreshadow_ledger import ForeshadowLedger, ForeshadowItem

logger = logging.getLogger(__name__)


# ==================== Prompt 定义 ====================

# BATCH_PROMPT 的 system 部分（固定指令，跨批次可缓存）
BATCH_SYSTEM_PROMPT = """你是专业的网络小说结构分析师。请对该段内容进行综合分析，严格按以下7个部分输出（纯 Markdown，不要输出任何 JSON）：

## 1. 主线推进
- 这段推进了哪些具体剧情线（用具体事件说明）
- 每条剧情线在这段中的起点和终点

## 2. 角色发展
- 主要角色在这段中的变化（实力、性格、关系）
- 角色之间的关系变化（盟友/敌对/暧昧等）

## 3. 伏笔管理
- 新设伏笔：列出具体伏笔内容（必须是跨章节的悬念/谜团）
- 已回收伏笔：列出回收方式（自然揭示/角色发现/剧情触发）
- 仍活跃伏笔：列出当前状态

## 4. 势力格局
- 各势力的实力变化（增强/削弱/消亡）
- 势力之间的冲突与合作

## 5. 世界观扩展
- 新增的世界观元素（地点、规则、势力、技术等）
- 已有元素的深化或修正

## 6. 节奏与转折
- 这段内容的节奏曲线（标注每几章的节奏变化）
- 关键转折点及其意义

## 7. 逻辑检查
- 是否存在逻辑漏洞或前后矛盾
- 角色行为是否符合其性格设定

【伏笔的定义】
伏笔是指：
1. 作者为后续情节埋下的悬念或线索
2. 故事中尚未解答的疑问
3. 需要跨越多个章节才能看到结果的事件设定

不属于伏笔的内容：
- 单章内发生并立即解决的小冲突
- 角色的瞬时情绪
- 直接陈述的事实

如果某个事件或悬念在本章节中已经完整解决，它不应该作为新伏笔输出。

数据字段说明：
- events: 本章核心事件列表（用于分析"主线推进"和"势力格局"）
- arcs: 角色变化列表，格式为"角色名→变化描述"（用于分析"角色发展"）
- pacing: 本章节奏判断（用于分析"节奏与转折"）
- summary: 承上启下摘要（剧情流串联）
- foreshadow: 伏笔线索（已精简，伏笔管理请参考上方伏笔清单）
- holes: 已知逻辑漏洞
- timeline: 时间线"""

# BATCH_PROMPT 的 user 部分（每批次变化，无法缓存）
BATCH_USER_TEMPLATE = """以下是《{book_name}》第{start}章到第{end}章的结构化分析数据。

【截至本批已识别的伏笔清单】
以下是从前文分析中提取的伏笔，请在分析时关注这些伏笔在本批次中的状态变化（是否被回收、是否有新进展）：
{foreshadow_catalog}

【字数要求】撰写的分卷总结字数要求在{batch_min_words}字以上，请详细展开分析，不要省略。

数据：
{batch_data}"""


# 第二次调用专用：只做伏笔 reconciliation 判断（纯 JSON 输出）
# system 部分（固定指令，跨批次可缓存）
RECONCILIATION_SYSTEM_PROMPT = """你是伏笔状态审计员。你的任务：逐一判断每个【活跃伏笔】在本批次中是否被回收。

【回收判断标准】
- 回收 = 伏笔的核心悬念在本批次中得到了明确答案，或驱动它的事件已经完结
- 仅仅是"被提及"或"继续推进"不算回收，必须要有结论性的解决
- 一个伏笔绝不可能在"埋设的同一批次"就被回收

【输出要求 - 极其重要】
只输出一个 JSON 对象，不要输出任何 Markdown、解释或代码块标记。
所有活跃伏笔都必须出现在 foreshadow_reconciliation 数组中，不允许遗漏。
不要输出 new_foreshadows 字段，伏笔总表已由程序管理，你只需判断回收。

**省 token 规则**：未回收的伏笔省略 reason 字段，只保留 foreshadow_id 和 is_resolved_in_this_batch。
只有被回收的伏笔才需要 resolved_chapter 和 resolution_summary。

JSON schema：
{{
  "foreshadow_reconciliation": [
    {{"foreshadow_id": "fs_xxx", "is_resolved_in_this_batch": true, "resolved_chapter": 18, "resolution_summary": "如何被回收"}},
    {{"foreshadow_id": "fs_yyy", "is_resolved_in_this_batch": false}}
  ]
}}

【示例】
活跃伏笔 fs_狸猫换太子真相，描述"刘妃、郭槐密谋以狸猫换走太子，陷害李妃"。
若本批次卷摘要中第18章"狄后讲述真相，仁宗认母，刘后惊死"，则：
{{"foreshadow_id": "fs_狸猫换太子真相", "is_resolved_in_this_batch": true, "resolved_chapter": 18, "resolution_summary": "狄后讲述真相，仁宗认母，刘后惊死，阴谋完全揭露"}}"""

# user 部分（每批次变化）
RECONCILIATION_USER_TEMPLATE = """【本批次卷摘要】（第{start}章-第{end}章）
{batch_summary}

【当前伏笔清单】（从原始分析数据提取，而非账本）
请对照以下伏笔的原始描述，判断每个伏笔在本批次中是否被回收。
{active_foreshadows_block}"""


# 最终报告生成专用 prompt
# system 部分（固定指令）
FINAL_SYSTEM_PROMPT = """你是专业的网络小说结构分析师。请产出完整的全书脉络报告，严格按以下8个部分输出：

## 1. 故事主线
用尽量详细的话语描述核心主线的变迁，包括：
- 故事的起点（开局设定）
- 主线的推进阶段（每个阶段的关键事件）
- 故事的终点（结局走向）
- 主线是否有断裂或偏移

## 2. 角色弧光
- 主角的完整成长轨迹（从起点到终点，中间的关键节点）
- 重要配角的关键转折点
- 角色之间的关系演变（从初始关系到最终关系）
- 是否有角色被浪费（出场多但作用小）

## 3. 伏笔网络（必须包含此节）
- 按时间线列出伏笔的"埋设→激活→回收"链
- 标注哪些伏笔回收得漂亮（意料之外，情理之中）
- 标注哪些伏笔虎头蛇尾（设了没回收，或回收太草率）
- 列出仍未回收的伏笔
- 所有伏笔结论必须带证据章节号

## 4. 世界观演变
- 从开头到结尾的变化
- 有没有换地图（场景迁移）
- 主要世界战力的变化（升级/降级/新势力出现）
- 整体技术水平的变化
- 整个世界的背景有没有发生迁移（如从校园到战场，从地球到星际）

## 5. 主题思想
- 贯穿全书的核心主题
- 主题如何通过情节和角色体现
- 主题是否有演变或深化
- 是否有主题偏离的情况

## 6. 节奏曲线
- 按章节区间标注节奏（紧张/舒缓/高潮/过渡）
- 标注全书的高潮点和低谷点
- 评价节奏是否合理（是否有拖沓或赶进度的问题）

## 7. 质量评价
- 写得好的段落及原因（对话精彩、情节紧凑、角色立体等）
- 有提升空间的段落及问题（节奏拖沓、角色行为不合理、伏笔遗忘等）
- 整体质量评分（1-10分）

## 8. 写作风格分析
- 基于输入的【写作风格特征】数据，分析本书的写作特点
- 涵盖：句式节奏、对话风格、修辞偏好、情感表达方式、叙事视角、信息管控策略
- 引用原文片段或统计数据支撑每个结论
- 如果输入中包含"写作风格特征"段落，必须基于该数据展开分析，不可忽略"""

# user 部分（每次调用变化）
FINAL_USER_TEMPLATE = """以下是《{book_name}》全书各段的分析摘要。

{style_profile}

{foreshadow_context}

{foreshadow_audit}

【字数要求】撰写的报告字数要求在{final_min_words}字以上，请详细展开分析，不要省略。

各段分析：
{volume_summaries}"""


# 全书伏笔复检专用 prompt
# system 部分（固定指令，跨批次可缓存）
GLOBAL_RECHECK_SYSTEM_PROMPT = """你是伏笔审计专家。你的任务：逐一检查每个伏笔，判断它在全书范围内是否已经被回收。

【回收判断标准】
- 回收 = 伏笔的核心悬念在全书中得到了明确答案，或驱动它的事件已经完结
- 关键线索：如果伏笔描述的问题在后续章节中有了明确交代，就是回收了
- 不要因为伏笔"影响深远"就认为它没回收——很多伏笔在解决后仍然影响后续剧情

【输出要求】
只输出一个 JSON 对象，不要输出任何 Markdown、解释或代码块标记。
所有活跃伏笔都必须出现在结果中，不允许遗漏。

**省 token 规则**：未回收的伏笔省略 reason 字段，只保留 foreshadow_id 和 is_resolved_in_this_batch。
只有被回收的伏笔才需要 resolved_chapter 和 resolution_summary。

JSON schema：
{{
  "foreshadow_reconciliation": [
    {{"foreshadow_id": "fs_xxx", "is_resolved_in_this_batch": true, "resolved_chapter": 实际回收的章节号, "resolution_summary": "如何被回收（引用具体章节）"}},
    {{"foreshadow_id": "fs_yyy", "is_resolved_in_this_batch": false}}
  ]
}}"""

# user 部分（每批次变化）
GLOBAL_RECHECK_USER_TEMPLATE = """以下是《{book_name}》的全书各卷摘要，以及一份仍标记为活跃的伏笔清单。

这些伏笔在逐批 reconciliation 中没有被标记为回收，但它们可能实际上在全书中已经被回收了——只是逐批判断时因为批次范围太小而遗漏。

【全书各卷摘要】
{volume_summaries}

【待检查的活跃伏笔清单】
{active_foreshadows_block}"""


# ==================== 辅助函数 ====================

def detect_book_name(output_dir: Path) -> Optional[str]:
    """
    检测书名（不带《》包裹，供报告模板使用）。
    1. 优先读取 blocks/metadata.json 中的 title 字段（切分时推断出的书名最准确）。
    2. 其次使用 blocks 所在目录名（书目目录名），去掉《》和括号备注。
    3. 不再扫描 blocks 内 .txt 文件名，避免 split_report.txt 等报告文件被误判为书名。
    """
    def _clean(name: str) -> str:
        name = name.strip()
        # 先去掉尾部括号备注，如 "（精校版全本）"
        name = re.sub(r"\s*[（(].*?[）)]\s*$", "", name)
        # 再去掉首尾的《》【】
        name = re.sub(r"^[《【]", "", name)
        name = re.sub(r"[》】]$", "", name)
        return name.strip()

    # 常见的非书名文本（metadata 推断错误时会抓到这些）
    _NON_TITLE_PREFIXES = (
        "内容简介", "作品简介", "小说简介", "简介", "作者", "目录", "楔子",
        "序章", "前言", "序言", "卷首语", "第一章", "第1章", "第 1 章",
        "正文", "章节",
    )

    def _looks_valid(title: str) -> bool:
        if not title or len(title) < 2 or len(title) > 60:
            return False
        # 如果 title 主要由等号/横线/星号/下划线/空格/竖线组成，视为无效
        if re.fullmatch(r"[\s\=\-\*\_\.\|┅┄━─]+", title):
            return False
        # 以冒号结尾，或看起来像章节/简介/作者/目录开头，视为无效
        if title.rstrip().endswith(("：", ":")):
            return False
        t = title.strip()
        if any(t.startswith(p) or t == p for p in _NON_TITLE_PREFIXES):
            return False
        return True

    blocks_dir = output_dir.parent / "blocks"
    if not blocks_dir.exists():
        return None

    meta_path = blocks_dir / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            title = _clean(meta.get("title", ""))
            if _looks_valid(title):
                return title
        except Exception:
            pass

    # 回退：用 blocks 父目录名（书目目录名），同样清理书名号与括号
    fallback = _clean(output_dir.parent.name)
    return fallback if _looks_valid(fallback) else None


def extract_key_fields(data: dict) -> Optional[dict]:
    """从 chapter_N_result.json 提取关键字段（用于分卷总结）"""
    # 健壮性：畸形 result 文件（字段类型不符）不应拖垮整个总结任务，
    # 统一兜底为安全的 dict/list，解析失败时返回 None（该章被跳过）。
    if not isinstance(data, dict):
        return None
    cb = data.get('cross_block') or {}
    lci = data.get('long_context_insights') or {}
    uk = data.get('updated_knowledge') or {}
    if not isinstance(cb, dict):
        cb = {}
    if not isinstance(lci, dict):
        lci = {}
    if not isinstance(uk, dict):
        uk = {}

    summary = cb.get('summary', '')
    if not summary:
        return None

    # core_events：只取 event 描述，丢掉 characters/function（省 token）
    core_events = data.get('core_events') or []
    if not isinstance(core_events, list):
        core_events = []
    events = [str(e.get('event', '')) for e in core_events
              if isinstance(e, dict) and e.get('event')]

    # character_arcs：只取 name + change_delta，丢掉 surface_action/inner_motivation/driver
    character_arcs = data.get('character_arcs') or []
    if not isinstance(character_arcs, list):
        character_arcs = []
    arcs = [f"{a.get('name', '')}→{a.get('change_delta', '')}"
            for a in character_arcs
            if isinstance(a, dict) and a.get('name')]

    # foreshadowing：只取第 1 条 clue（伏笔 reconciliation 已在第二次调用单独处理，
    # 第一次调用只需给 LLM 一个伏笔印象，不需要全量列表）
    foreshadows = data.get('foreshadowing') or []
    if not isinstance(foreshadows, list):
        foreshadows = []
    first_clue = []
    if foreshadows and isinstance(foreshadows[0], dict):
        clue = foreshadows[0].get('clue', '')
        if clue:
            first_clue = [clue]

    plot_holes = data.get('plot_holes') or []
    if not isinstance(plot_holes, list):
        plot_holes = []

    return {
        'ch': data.get('chapter_number', 0),
        'summary': summary,
        'link': cb.get('contextual_link', ''),
        'foreshadow': first_clue,
        'activeFS': lci.get('active_foreshadowing', []),
        'resolvedFS': lci.get('resolved_foreshadowing', []),
        'holes': plot_holes,
        'timeline': uk.get('timeline', ''),
        'events': events,
        'arcs': arcs,
        'pacing': lci.get('pacing', ''),
    }


def extract_foreshadow_json(response: str) -> Optional[dict]:
    """
    从LLM响应中提取伏笔 reconciliation JSON。
    """
    if not response:
        return None

    data: Optional[dict] = None

    # 策略1: 直接解析（第二次调用期望纯 JSON）
    data = safe_parse_json(response.strip())

    # 策略2: 从 markdown 代码块 / 残缺文本中兜底提取
    if not data:
        json_str = extract_json_from_text(response)
        if json_str:
            data = safe_parse_json(json_str)

    if not data:
        logger.warning(f"伏笔 reconciliation JSON 解析失败，响应前200字: {response[:200]}")
        return None

    # 处理列表类型
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                data = item
                break
        else:
            logger.warning(f"伏笔 reconciliation 返回了非对象列表")
            return None

    if not isinstance(data, dict):
        logger.warning(f"伏笔 reconciliation 返回了非字典类型: {type(data).__name__}")
        return None

    # 新格式：foreshadow_reconciliation
    if 'foreshadow_reconciliation' in data:
        recon = data.get('foreshadow_reconciliation', []) or []
        logger.info(f"伏笔 reconciliation 解析成功: {len(recon)} 条")
        return {
            'reconciliation': recon,
            'new_foreshadows': data.get('new_foreshadows', []) or []
        }

    # 兼容旧格式：foreshadow_evidence
    if 'foreshadow_evidence' in data:
        evidence = data['foreshadow_evidence'] or {}
        reconciliation = []
        for item in evidence.get('resolved_foreshadows', []) or []:
            reconciliation.append({
                'foreshadow_id': item.get('id', ''),
                'is_resolved_in_this_batch': True,
                'resolved_chapter': item.get('resolved_chapter', 0),
                'resolution_summary': item.get('resolution', '')
            })
        return {
            'reconciliation': reconciliation,
            'new_foreshadows': evidence.get('new_foreshadows', []) or []
        }

    return None


def validate_reconciliation_json(content: str) -> Tuple[bool, str]:
    """校验第二次调用返回的是合法 reconciliation JSON"""
    result = extract_foreshadow_json(content)
    if result is None:
        return False, "无法解析为 foreshadow_reconciliation JSON"
    recon = result.get('reconciliation')
    if not isinstance(recon, list):
        return False, "foreshadow_reconciliation 必须是数组"
    # 验证：LLM 被要求不输出 new_foreshadows，记录实际情况
    new_fs = result.get('new_foreshadows')
    if new_fs:
        logger.warning(f"LLM 意外输出了 new_foreshadows（{len(new_fs)}条），按 prompt 指令应省略")
    return True, ""


def build_reconciliation_retry_messages(validation_error: str, messages: List[dict]) -> List[dict]:
    """解析失败时追加格式修正提示后重试"""
    hint = (
        f"\n\n格式错误: {validation_error}\n"
        "请重新输出，且只输出一个 JSON 对象（不要 Markdown、不要代码块标记）。"
        "结构必须包含数组字段 foreshadow_reconciliation。"
    )
    new_messages = list(messages)
    if new_messages and new_messages[-1].get('role') == 'user':
        new_messages[-1] = {**new_messages[-1], 'content': new_messages[-1]['content'] + hint}
    else:
        new_messages.append({"role": "user", "content": hint.strip()})
    return new_messages


def extract_analysis_text(response: str) -> str:
    """提取第一次调用的 Markdown 卷摘要（剥离末尾可能的 JSON）"""
    if not response:
        return ""

    for marker in ('```json', '```JSON', '``` Json'):
        pos = response.find(marker)
        if pos != -1:
            return response[:pos].rstrip()

    for key in ('"foreshadow_reconciliation"', '"foreshadow_evidence"'):
        pos = response.find(key)
        if pos > 0:
            brace_pos = response.rfind('{', 0, pos)
            if brace_pos > 0:
                return response[:brace_pos].rstrip()

    return response.strip()


# ==================== Runner 类 ====================

class FinalSummaryRunner:
    """最终总结执行器（asyncio）

    回调（均为普通同步函数，在事件循环内调用）：
    - on_progress(dict)：进度消息，type 对齐旧 Qt 信号
      status/phase/batch_done/batch_failed/ledger_updated/complete
    - on_token_stats(dict)：{category, input_tokens, output_tokens}
    """

    def __init__(
        self,
        config: AppConfig,
        output_dir: Path,
        start_chapter: int,
        end_chapter: int,
        batch_size: int,
        concurrency: int = 1,
        ledger_path: Optional[Path] = None,
        on_progress: Optional[Callable[[dict], None]] = None,
        on_token_stats: Optional[Callable[[dict], None]] = None,
    ):
        self.config = config
        self.output_dir = Path(output_dir)
        self.start_chapter = start_chapter
        self.end_chapter = end_chapter
        self.batch_size = max(1, batch_size if batch_size is not None else 1)
        self.concurrency = max(1, concurrency if concurrency is not None else self.config.analysis.concurrency)
        self._lock = asyncio.Lock()
        self._stop_requested = False
        self._on_progress = on_progress
        self._on_token_stats = on_token_stats

        # 分类 token 累计
        self._tokens_summary = (0, 0)        # 卷摘要 (input, output)
        self._tokens_final = (0, 0)          # 最终报告
        self._tokens_reconciliation = (0, 0) # 伏笔 reconciliation（批次内）
        self._tokens_recheck = (0, 0)        # 全书伏笔复检
        self._tokens_style = (0, 0)          # 风格提取

        _api_cfg = replace(self.config.api, json_mode="default")
        self._llm = LLMClient(_api_cfg)

        self.ledger_path = ledger_path or (self.output_dir / "foreshadow_ledger.json")
        self.ledger = ForeshadowLedger.load(self.ledger_path)
        self.ledger.batch_size_snapshot = batch_size
        self.book_name = detect_book_name(self.output_dir) or "未知小说"
        logger.info(f"检测到书名: {self.book_name}")

    def stop(self):
        self._stop_requested = True
        self._llm.request_stop()

    def _emit_progress(self, payload: dict) -> None:
        if self._on_progress:
            try:
                self._on_progress(payload)
            except Exception as e:
                logger.error(f"progress 回调异常: {e}")

    def _load_results(self) -> List[dict]:
        """加载并提取所有章节结果的关键字段（同步 IO，经 to_thread 调用）"""
        results = []
        for f in self.output_dir.glob("chapter_*_result.json"):
            m = re.match(r'chapter_(\d+)_result\.json', f.name)
            if not m:
                continue
            ch = int(m.group(1))
            if ch < self.start_chapter or ch > self.end_chapter:
                continue
            data = safe_load_json(f)
            if data is None:
                continue
            extracted = extract_key_fields(data)
            if extracted:
                # 额外保留原始 foreshadowing 数据（用于构建伏笔总表）
                extracted["foreshadowing_raw"] = data.get("foreshadowing", [])
                results.append(extracted)
        results.sort(key=lambda x: x['ch'])
        return results

    def _format_batch(self, batch: List[dict]) -> str:
        return json.dumps(batch, ensure_ascii=False, indent=1)

    async def _call_llm_summary(self, prompt: str) -> Optional[str]:
        """第一次调用：产出 Markdown 卷摘要"""
        messages = [
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        success, content, error, tokens = await self._llm.chat_with_retry(
            messages, max_tokens=self.config.api.max_tokens, validate_response=None
        )
        self._record_tokens("summary", tokens)
        if success:
            return content
        logger.error(f"卷摘要 LLM 调用失败: {error}")
        return None

    async def _call_llm_final(self, prompt: str) -> Optional[str]:
        """最终报告调用：使用 FINAL_SYSTEM_PROMPT"""
        messages = [
            {"role": "system", "content": FINAL_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        success, content, error, tokens = await self._llm.chat_with_retry(
            messages, max_tokens=self.config.api.max_tokens, validate_response=None
        )
        self._record_tokens("final", tokens)
        if success:
            return content
        logger.error(f"最终报告 LLM 调用失败: {error}")
        return None

    async def _call_llm_reconciliation(self, prompt: str) -> Optional[dict]:
        """第二次调用：产出伏笔 reconciliation JSON"""
        messages = [
            {"role": "system", "content": RECONCILIATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        success, content, error, tokens = await self._llm.chat_with_retry(
            messages, max_tokens=self.config.api.max_tokens,
            validate_response=validate_reconciliation_json,
            retry_messages_builder=build_reconciliation_retry_messages
        )
        self._record_tokens("reconciliation", tokens)
        if not success:
            logger.warning(f"伏笔 reconciliation 调用失败: {error}")
            return None
        return extract_foreshadow_json(content)

    async def _call_llm_global_recheck(self, prompt: str) -> Optional[dict]:
        """全书复检调用：使用 GLOBAL_RECHECK_SYSTEM_PROMPT"""
        messages = [
            {"role": "system", "content": GLOBAL_RECHECK_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        success, content, error, tokens = await self._llm.chat_with_retry(
            messages, max_tokens=self.config.api.max_tokens,
            validate_response=validate_reconciliation_json,
            retry_messages_builder=build_reconciliation_retry_messages
        )
        self._record_tokens("recheck", tokens)
        if not success:
            logger.warning(f"全书复检调用失败: {error}")
            return None
        return extract_foreshadow_json(content)

    def _record_tokens(self, category: str, tokens) -> None:
        """累积并发出分类 token 统计（事件循环内调用，无需缓冲）"""
        if not tokens or not isinstance(tokens, tuple) or len(tokens) < 2:
            return
        in_tok = int(tokens[0] or 0)
        out_tok = int(tokens[1] or 0)
        if category == "summary":
            self._tokens_summary = (self._tokens_summary[0] + in_tok,
                                    self._tokens_summary[1] + out_tok)
        elif category == "final":
            self._tokens_final = (self._tokens_final[0] + in_tok,
                                  self._tokens_final[1] + out_tok)
        elif category == "reconciliation":
            self._tokens_reconciliation = (self._tokens_reconciliation[0] + in_tok,
                                           self._tokens_reconciliation[1] + out_tok)
        elif category == "recheck":
            self._tokens_recheck = (self._tokens_recheck[0] + in_tok,
                                    self._tokens_recheck[1] + out_tok)
        elif category == "style":
            self._tokens_style = (self._tokens_style[0] + in_tok,
                                  self._tokens_style[1] + out_tok)
        if self._on_token_stats:
            try:
                self._on_token_stats({
                    "category": category,
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                })
            except Exception as e:
                logger.error(f"token_stats 回调异常: {e}")

    def get_token_stats(self) -> Dict[str, Dict[str, int]]:
        """分类 token 统计快照"""
        return {
            category: {"input_tokens": pair[0], "output_tokens": pair[1]}
            for category, pair in [
                ("summary", self._tokens_summary),
                ("final", self._tokens_final),
                ("reconciliation", self._tokens_reconciliation),
                ("recheck", self._tokens_recheck),
                ("style", self._tokens_style),
            ]
        }

    # 重要伏笔类型（用于过滤低价值条目，如"能力展示"、"细节"等不算伏笔）
    IMPORTANT_TYPES = {
        "悬念", "线索", "伏笔", "世界观伏笔", "情节伏笔", "角色伏笔",
        "角色命运", "角色背景", "世界观揭秘", "设定揭示", "剧情伏笔",
        "角色关系伏笔", "角色成长伏笔", "威胁预告", "威胁预警",
        "世界观线索", "世界观铺垫/伏笔", "设定伏笔", "关键道具/伏笔",
        "情节钩子", "身份/关系伏笔", "世界观与阴谋伏笔", "伏笔升级/矛盾",
        "悬念/未解之谜", "新线索", "信息差",
    }

    def _build_foreshadow_catalog(self, results: List[dict], max_items: int = 300) -> list:
        """
        从逐章分析结果的 foreshadowing 字段构建伏笔总表。
        纯代码扫描，不调用 LLM。用 deduplicate_foreshadows 去重。
        提取 type 和 confidence 字段，用于过滤低价值条目。

        Args:
            results: _load_results 的返回值
            max_items: 总表最大条目数（超出按 confidence 排序截断）
        """
        all_clues = []
        for r in results:
            ch = r.get("ch", 0)
            foreshadows = r.get("foreshadowing_raw", [])
            if not foreshadows:
                continue
            for fs in foreshadows:
                if not isinstance(fs, dict):
                    continue
                clue = fs.get("clue", "")
                if not clue or len(clue.strip()) < 15:
                    continue
                ftype = fs.get("type", "")
                conf = fs.get("confidence", "中")
                all_clues.append((ch, clue, ftype, conf))

        logger.info(f"伏笔总表原始数据：{len(all_clues)} 条线索")

        # 按重要度过滤：只保留重要类型的伏笔（不管置信度）
        filtered = [
            item for item in all_clues
            if item[2] in self.IMPORTANT_TYPES
        ]
        # 记录被丢弃的类型，帮助发现 LLM 产出的意料之外的类型
        dropped_types = {}
        for item in all_clues:
            if item[2] not in self.IMPORTANT_TYPES:
                dropped_types[item[2]] = dropped_types.get(item[2], 0) + 1
        if dropped_types:
            logger.info(f"IMPORTANT_TYPES 过滤丢弃: {dropped_types}")
        logger.info(f"重要度过滤后：{len(filtered)} 条（重要类型，原 {len(all_clues)} 条）")

        catalog = deduplicate_foreshadows(filtered)

        # 按置信度排序，截断到 max_items
        conf_order = {"高": 3, "中": 2, "低": 1}
        catalog.sort(key=lambda c: conf_order.get(c.get("confidence", ""), 0), reverse=True)
        if len(catalog) > max_items:
            logger.info(f"伏笔总表截断：{len(catalog)} -> {max_items} 条")
            catalog = catalog[:max_items]

        self._next_foreshadow_id = len(catalog) + 1
        logger.info(f"伏笔总表构建完成：{len(all_clues)} 条原始 -> {len(filtered)} 条过滤 -> {len(catalog)} 条去重")
        return catalog

    def _format_catalog_for_prompt(self, catalog: list, max_chapter: int = 99999) -> str:
        """
        格式化截至某章节的伏笔表，用于注入 BATCH_PROMPT / RECONCILIATION_PROMPT。
        格式：[fs_001] type|clue (ch1,15,837)
        """
        if not catalog:
            return "（暂无伏笔记录）\n"

        lines = []
        for item in catalog:
            if item["first_seen"] > max_chapter:
                continue
            chapters_str = ",".join(str(c) for c in item["evidence_chapters"][:8])
            merged = f" (合并{item['merged_count']}条)" if item["merged_count"] > 1 else ""
            ftype = item.get("type", "")
            type_prefix = f"[{ftype}] " if ftype else ""
            lines.append(f"[{item['id']}] {type_prefix}{item['clue']} (ch{chapters_str}){merged}")

        if not lines:
            return "（暂无伏笔记录）\n"

        return "\n".join(lines) + "\n"

    def _allocate_foreshadow_id(self) -> str:
        """分配一个新的伏笔 id（顺序递增，不与总表重复）"""
        fid = f"fs_{self._next_foreshadow_id:03d}"
        self._next_foreshadow_id += 1
        return fid

    def _build_active_foreshadows_block(self, ch_start: int, ch_end: int, foreshadow_catalog: list = None) -> str:
        """构建【当前伏笔清单】文本块，使用伏笔总表的原始 clue"""
        if foreshadow_catalog:
            # 从伏笔总表取截至本批的条目
            items = [c for c in foreshadow_catalog if c["first_seen"] <= ch_end]
        else:
            # 降级：用账本活跃列表
            active_items = [i for i in self.ledger.items if i.status == 'active']
            items = [{"id": i.id, "clue": i.description, "evidence_chapters": i.evidence_chapters,
                      "first_seen": i.first_seen_chapter, "last_seen": i.last_seen_chapter}
                     for i in active_items]

        if not items:
            return "\n【当前伏笔清单】\n（暂无伏笔，foreshadow_reconciliation 输出空数组 [] 即可）\n"

        lines = [
            f"\n【当前伏笔清单 - 共{len(items)}个，必须逐一判断是否在第{ch_start}-{ch_end}章中被回收】",
            "（注意：仅当伏笔核心悬念在本批次中得到结论性解决时才标记回收；仅被提及/继续推进不算回收）\n"
        ]
        for i, item in enumerate(items, 1):
            eid = item["id"]
            clue = item["clue"]
            chapters_str = ",".join(str(c) for c in item.get("evidence_chapters", [])[:5])
            first = item.get("first_seen", 0)
            last = item.get("last_seen", 0)
            lines.append(f'{i}. id="{eid}"  描述: {clue}')
            lines.append(f"   首次出现: 第{first}章  最近出现: 第{last}章  证据: [{chapters_str}]")

        return "\n".join(lines)

    def _build_foreshadow_context(self, foreshadow_catalog: list = None) -> str:
        """构建供 FINAL_PROMPT 使用的伏笔上下文——伏笔总表 clue + 账本状态合并"""
        if not self.ledger.items and not foreshadow_catalog:
            return ""

        # 建立账本 id -> item 的映射
        ledger_map = {i.id: i for i in self.ledger.items}

        # 合并：伏笔总表的 clue + 账本的状态
        all_items = []
        catalog_ids = set()
        if foreshadow_catalog:
            for cat in foreshadow_catalog:
                lid = cat["id"]
                catalog_ids.add(lid)
                ledger_item = ledger_map.get(lid)
                all_items.append({
                    "id": lid,
                    "clue": cat["clue"],
                    "first_seen": cat["first_seen"],
                    "last_seen": cat["last_seen"],
                    "evidence_chapters": cat["evidence_chapters"],
                    "status": ledger_item.status if ledger_item else "active",
                    "resolution": ledger_item.resolution if ledger_item else None,
                    "resolved_chapter": ledger_item.resolved_chapter if ledger_item else None,
                })

        # 补充账本中有但总表中没有的条目（兜底）
        for lid, item in ledger_map.items():
            if lid not in catalog_ids:
                all_items.append({
                    "id": lid,
                    "clue": item.description,
                    "first_seen": item.first_seen_chapter,
                    "last_seen": item.last_seen_chapter,
                    "evidence_chapters": item.evidence_chapters,
                    "status": item.status,
                    "resolution": item.resolution,
                    "resolved_chapter": item.resolved_chapter,
                })

        if not all_items:
            return ""

        active = [i for i in all_items if i["status"] == "active"]
        resolved = [i for i in all_items if i["status"] == "resolved"]
        dormant = [i for i in all_items if i["status"] == "dormant"]

        parts = []
        parts.append(f"## 伏笔总表（共{len(all_items)}条：活跃{len(active)} / 已回收{len(resolved)} / 休眠{len(dormant)}）\n")

        if resolved:
            parts.append("### 已回收伏笔")
            for item in resolved:
                chapters_str = ",".join(str(c) for c in item["evidence_chapters"][:5])
                parts.append(f'- [{item["id"]}] {item["clue"]} (ch{chapters_str})')
                res = item.get("resolution") or "未记录"
                rch = item.get("resolved_chapter") or "?"
                parts.append(f'  - 回收: ch{rch}, {res}')
            parts.append("")

        if active:
            parts.append("### 仍活跃伏笔（请逐一分析是否在全书中已被回收）")
            for item in active:
                chapters_str = ",".join(str(c) for c in item["evidence_chapters"][:5])
                parts.append(f'- [{item["id"]}] {item["clue"]} (ch{chapters_str})')
            parts.append("")

        if dormant:
            parts.append("### 休眠伏笔（长期未出现，可能是作者遗忘）")
            for item in dormant:
                parts.append(f'- [{item["id"]}] {item["clue"]} (ch{item["first_seen"]}-{item["last_seen"]})')
            parts.append("")

        parts.append("请在报告中特别关注：")
        parts.append('1. 标记为"已回收"的伏笔，分析其回收质量')
        parts.append('2. 标记为"休眠"的伏笔，这些可能是作者遗忘的伏笔')
        parts.append('3. 标记为"活跃"的伏笔——**请逐一核对**，判断它们在全书中是否实际上已经被回收了')
        parts.append("4. 所有伏笔结论必须带证据章节号")

        return "\n".join(parts)

    async def _run_style_extraction(self) -> Optional[dict]:
        """调用 style_analyzer 提取风格特征"""
        # 优先书目录下的 blocks（web 版按书目录组织），降级 working_directory
        blocks_dir = self.output_dir.parent / "blocks"
        if not blocks_dir.exists() and self.config.working_directory:
            blocks_dir = Path(self.config.working_directory) / "blocks"
        if not blocks_dir.exists():
            logger.warning(f"风格分析：blocks 目录不存在: {blocks_dir}")
            return None

        api_config = {
            'base_url': self.config.api.base_url,
            'api_key': self.config.api.api_key,
            'model': self.config.api.model,
        }

        try:
            return await extract_style_profile(blocks_dir, self.book_name, api_config)
        except Exception as e:
            logger.warning(f"风格分析失败: {e}")
            return None

    def _format_style_for_prompt(self, style_profile: dict) -> str:
        """将 style_profile 格式化为自然语言段落，注入最终报告 prompt"""
        agg = style_profile.get("statistical", {})
        sem = style_profile.get("semantic", {})

        parts = ["## 写作风格特征\n"]

        # 统计部分
        parts.append("### 定量指标")
        parts.append(f"- 平均句长 {agg.get('avg_sentence_length', 0):.1f} 字，"
                     f"短句(≤10字) {agg.get('short_sentence_ratio', 0):.1%}，"
                     f"长句(≥50字) {agg.get('long_sentence_ratio', 0):.1%}")
        parts.append(f"- 对话占比 {agg.get('dialogue_ratio', 0):.1%}，"
                     f"词汇丰富度(TTR) {agg.get('ttr', 0):.2f}")
        parts.append(f"- 感官词密度 {agg.get('sensory_density', 0):.1f}/千字，"
                     f"比喻词频率 {agg.get('metaphor_freq', 0):.1f}/千字")
        parts.append(f"- 文言词占比 {agg.get('literary_ratio', 0):.2f}%，"
                     f"口语词占比 {agg.get('colloquial_ratio', 0):.2f}%")

        # 语义部分
        parts.append("\n### 定性特征")
        for key, label in [
            ('signature_expressions', '标志性表达'),
            ('narrative_rhythm', '叙事节奏'),
            ('dialogue_style', '对话风格'),
            ('rhetorical_preferences', '修辞偏好'),
            ('emotional_expression', '情感表达'),
            ('narrative_voice', '叙事视角'),
            ('information_control', '信息管控'),
            ('narrator_and_genre', '叙述者与类型'),
        ]:
            value = sem.get(key, "")
            if value and '未提取' not in value and '提取失败' not in value:
                parts.append(f"- **{label}**：{value}")

        return "\n".join(parts)

    def _apply_reconciliation(self, batch_idx: int, ch_start: int, ch_end: int, foreshadow_data: Optional[dict]) -> None:
        """应用 LLM 的 reconciliation 结果到账本"""
        if foreshadow_data is None:
            logger.warning(f"批次{batch_idx}：reconciliation 缺失，伏笔状态保持不变")
            self.ledger.reconcile_and_update(batch_idx)
            return
        reconciliation = foreshadow_data.get('reconciliation', []) or []
        for item in reconciliation:
            if not isinstance(item, dict):
                continue
            foreshadow_id = item.get('foreshadow_id', '')
            if not foreshadow_id:
                continue
            existing = self.ledger._find_item(foreshadow_id)
            is_resolved = bool(item.get('is_resolved_in_this_batch', False))
            if is_resolved:
                if existing:
                    existing.status = 'resolved'
                    existing.resolution = item.get('resolution_summary') or item.get('reason') or ''
                    existing.resolved_chapter = item.get('resolved_chapter') or ch_end
                    existing.resolved_batch = batch_idx
                    existing.source_type = 'llm_judged'
                    existing.last_seen_chapter = ch_end
                    existing.last_seen_batch = batch_idx
                    logger.info(f"LLM标记伏笔回收: {foreshadow_id} (第{existing.resolved_chapter}章)")
            else:
                if existing:
                    existing.last_seen_chapter = ch_end
                    existing.last_seen_batch = batch_idx
        self.ledger.reconcile_and_update(batch_idx)

    async def _run_global_foreshadow_recheck(self, volume_summaries: List[str],
                                             foreshadow_catalog: list = None) -> None:
        """全书伏笔复检：对仍 active 的伏笔做一次全书范围的集中复检"""
        active_ids = {i.id for i in self.ledger.items if i.status == 'active'}
        if not active_ids:
            logger.info("无活跃伏笔，跳过全书复检")
            return

        # 从伏笔总表取活跃伏笔的原始 clue（比账本 description 更准确）
        if foreshadow_catalog:
            recheck_items = [c for c in foreshadow_catalog if c["id"] in active_ids]
        else:
            # 降级：用账本
            recheck_items = [{"id": i.id, "clue": i.description,
                              "first_seen": i.first_seen_chapter, "last_seen": i.last_seen_chapter,
                              "evidence_chapters": i.evidence_chapters}
                             for i in self.ledger.items if i.status == 'active']

        if not recheck_items:
            logger.info("无活跃伏笔，跳过全书复检")
            return

        logger.info(f"开始全书伏笔复检：{len(recheck_items)}个活跃伏笔")
        self._emit_progress({
            "type": "phase", "phase": "recheck", "phase_label": "全书伏笔复检",
            "message": f"[阶段2/4] 全书伏笔复检（{len(recheck_items)}个活跃伏笔）..."
        })
        full_text = "\n\n".join(volume_summaries)
        recheck_batch_size = 40
        total_rechecked = 0

        # 准备所有复检子批次
        recheck_batches = []
        for batch_start in range(0, len(recheck_items), recheck_batch_size):
            batch_items = recheck_items[batch_start:batch_start + recheck_batch_size]
            lines = []
            for i, item in enumerate(batch_items, 1):
                chapters_str = ",".join(str(c) for c in item.get("evidence_chapters", [])[:5])
                lines.append(f'{i}. id="{item["id"]}"  描述: {item["clue"]}')
                lines.append(f"   首次出现: 第{item.get('first_seen', 0)}章  最近出现: 第{item.get('last_seen', 0)}章  证据: [{chapters_str}]")
            recheck_prompt = GLOBAL_RECHECK_USER_TEMPLATE.format(
                book_name=self.book_name, volume_summaries=full_text,
                active_foreshadows_block='\n'.join(lines)
            )
            recheck_batches.append((batch_items, recheck_prompt))

        semaphore = asyncio.Semaphore(min(self.concurrency, len(recheck_batches)))

        async def process_recheck_batch(prompt: str) -> int:
            async with semaphore:
                if self._stop_requested:
                    return 0
                foreshadow_data = await self._call_llm_global_recheck(prompt)
            if foreshadow_data is None:
                return 0
            local_count = 0
            for item in foreshadow_data.get('reconciliation', []) or []:
                if not isinstance(item, dict):
                    continue
                foreshadow_id = item.get('foreshadow_id', '')
                if not foreshadow_id:
                    continue
                async with self._lock:
                    existing = self.ledger._find_item(foreshadow_id)
                    is_resolved = bool(item.get('is_resolved_in_this_batch', False))
                    if is_resolved and existing and existing.status == 'active':
                        existing.status = 'resolved'
                        existing.resolution = item.get('resolution_summary') or item.get('reason') or ''
                        existing.resolved_chapter = item.get('resolved_chapter') or 0
                        existing.source_type = 'llm_judged'
                        logger.info(f"全书复检回收: {foreshadow_id} (第{existing.resolved_chapter}章)")
                        local_count += 1
            return local_count

        tasks = [asyncio.create_task(process_recheck_batch(prompt))
                 for _, prompt in recheck_batches]
        for coro in asyncio.as_completed(tasks):
            try:
                total_rechecked += await coro
            except Exception as e:
                logger.error(f"复检批次异常: {e}")

        logger.info(f"全书伏笔复检完成，新回收 {total_rechecked} 个伏笔")
        if total_rechecked > 0:
            self._emit_progress({"type": "ledger_updated", "counts": self.ledger.get_item_count()})

    async def run(self) -> Optional[str]:
        """执行完整总结流程。返回最终报告文本；用户停止返回 None；失败抛 RuntimeError"""
        t_start = time.time()
        self._emit_progress({"type": "status", "message": "正在加载章节数据..."})
        results = await asyncio.to_thread(self._load_results)
        if not results:
            raise RuntimeError("未找到有效的章节分析结果")
        total_chapters = len(results)
        total_batches = (total_chapters + self.batch_size - 1) // self.batch_size
        # 账本总章数用真实加载到的章节数（而非 end_chapter，未传时=999999 会让
        # 伏笔「N章未见即休眠」等判据分母失真，休眠判定失效）
        self.ledger.total_chapters = total_chapters
        logger.info(f"加载{total_chapters}章，分为{total_batches}批，每批{self.batch_size}章")

        # 构建伏笔总表（纯代码扫描，不调用 LLM）
        foreshadow_catalog = self._build_foreshadow_catalog(
            results, max_items=self.config.analysis.max_foreshadow_catalog)

        # 同步伏笔总表到账本（账本为空时初始化，让 reconciliation 有条目可更新）
        existing_ids = {i.id for i in self.ledger.items}
        added = 0
        for cat in foreshadow_catalog:
            if cat["id"] not in existing_ids:
                self.ledger.items.append(ForeshadowItem(
                    id=cat["id"], description=cat["clue"],
                    first_seen_chapter=cat["first_seen"], first_seen_batch=0,
                    last_seen_chapter=cat["last_seen"], last_seen_batch=0,
                    evidence_chapters=cat["evidence_chapters"],
                    confidence=0.8, source_type='catalog_scan'
                ))
                added += 1
        logger.info(f"伏笔总表同步到账本: {added} 条新增，{len(existing_ids)} 条已存在")

        batch_tasks = []
        for i in range(0, total_chapters, self.batch_size):
            batch = results[i:i + self.batch_size]
            batch_idx = len(batch_tasks) + 1
            ch_start = batch[0]['ch']
            ch_end = batch[-1]['ch']
            batch_data = self._format_batch(batch)
            catalog_text = self._format_catalog_for_prompt(foreshadow_catalog, max_chapter=ch_end)
            summary_prompt = BATCH_USER_TEMPLATE.format(
                book_name=self.book_name, start=ch_start, end=ch_end,
                batch_data=batch_data, foreshadow_catalog=catalog_text,
                batch_min_words=self.config.analysis.batch_summary_min_words
            )
            batch_tasks.append((batch_idx, ch_start, ch_end, summary_prompt))

        self._emit_progress({
            "type": "phase", "phase": "batch", "phase_label": "分卷分析+伏笔调和",
            "total_batches": total_batches,
            "message": f"[阶段1/4] 分卷分析+伏笔调和（{len(batch_tasks)}批，并发{self.concurrency}）..."
        })

        logger.info(f"开始并发卷摘要：{len(batch_tasks)}批，并发数={self.concurrency}")
        self._emit_progress({"type": "status", "message": f"正在并发分析{len(batch_tasks)}批（并发{self.concurrency}）..."})

        volume_results: Dict[int, str] = {}  # batch_idx -> summary text
        semaphore = asyncio.Semaphore(min(self.concurrency, len(batch_tasks)))

        async def process_batch_collect(idx: int, ch_start: int, ch_end: int, prompt: str):
            """并发采集：卷摘要 + reconciliation（两次 LLM 调用）"""
            async with semaphore:
                if self._stop_requested:
                    return None
                summary_response = await self._call_llm_summary(prompt)
                if self._stop_requested:
                    return None
                if summary_response is None:
                    # 失败批次不入 volume_results（仅记失败状态），避免占位文本污染最终报告；
                    # 否则 not volume_results 永不成立、『所有批次均失败』的 RuntimeError 变死代码。
                    return {"status": "failed", "idx": idx, "ch_start": ch_start, "ch_end": ch_end}

                volume_summary = extract_analysis_text(summary_response)
                volume_results[idx] = volume_summary
                elapsed = time.time() - t_start

                # 伏笔 reconciliation
                active_block = self._build_active_foreshadows_block(ch_start, ch_end, foreshadow_catalog)
                reconciliation_prompt = RECONCILIATION_USER_TEMPLATE.format(
                    book_name=self.book_name, start=ch_start, end=ch_end,
                    batch_summary=volume_summary,
                    active_foreshadows_block=active_block
                )
                foreshadow_data = await self._call_llm_reconciliation(reconciliation_prompt)
                return {
                    "status": "done", "idx": idx, "ch_start": ch_start, "ch_end": ch_end,
                    "volume_summary": volume_summary, "elapsed": elapsed,
                    "foreshadow_data": foreshadow_data,
                }

        tasks = [asyncio.create_task(process_batch_collect(idx, start, end, prompt))
                 for idx, start, end, prompt in batch_tasks]
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                if result is None:
                    continue
                if result["status"] == "done":
                    idx = result["idx"]
                    self._emit_progress({
                        "type": "batch_done", "batch": idx, "total_batches": total_batches,
                        "elapsed": result["elapsed"],
                        "message": f"[卷{idx}/{total_batches}] 完成"
                    })
                    async with self._lock:
                        self._apply_reconciliation(
                            idx, result["ch_start"], result["ch_end"], result["foreshadow_data"])
                elif result["status"] == "failed":
                    idx = result["idx"]
                    self._emit_progress({
                        "type": "batch_failed", "batch": idx,
                        "message": f"[卷{idx}] LLM调用失败，跳过"
                    })
            except Exception as e:
                logger.error(f"卷异常: {e}")

        if self._stop_requested:
            self._emit_progress({"type": "status", "message": "已停止"})
            return None
        if not volume_results:
            raise RuntimeError("所有批次均失败，无法生成总结")

        # 按 batch_idx 排序，恢复顺序
        volume_summaries = []
        for idx, ch_start, ch_end, _ in batch_tasks:
            text = volume_results.get(idx, "")
            if text:
                volume_summaries.append(f"=== 第{ch_start}-{ch_end}章 ===\n{text}")

        # 全书伏笔复检
        await self._run_global_foreshadow_recheck(volume_summaries, foreshadow_catalog)
        self.ledger.save(self.ledger_path)
        self._emit_progress({"type": "ledger_updated", "counts": self.ledger.get_item_count()})

        # 风格提取（前置步骤）
        self._emit_progress({
            "type": "phase", "phase": "style", "phase_label": "风格分析",
            "message": "[阶段3/4] 正在提取写作风格特征..."
        })
        style_profile = await self._run_style_extraction()
        style_text = self._format_style_for_prompt(style_profile) if style_profile else "（风格分析未完成）"

        # 生成最终报告
        self._emit_progress({
            "type": "phase", "phase": "report", "phase_label": "生成全书脉络报告",
            "message": "[阶段4/4] 正在生成全书脉络报告..."
        })
        foreshadow_context = self._build_foreshadow_context(foreshadow_catalog)
        audit_text = self.ledger.audit_text_for_report()
        final_prompt = FINAL_USER_TEMPLATE.format(
            book_name=self.book_name,
            volume_summaries="\n\n".join(volume_summaries),
            foreshadow_context=foreshadow_context,
            foreshadow_audit=audit_text,
            style_profile=style_text,
            final_min_words=self.config.analysis.final_report_min_words
        )
        final_report = await self._call_llm_final(final_prompt)
        if final_report is None:
            raise RuntimeError("最终汇总LLM调用失败")

        # 注意：style_profile 已通过 FINAL_USER_TEMPLATE 的 {style_profile} 注入最终报告 prompt，
        # 此处不再无条件追加，否则风格部分会在报告里出现两次。

        audit_path = self.output_dir / "foreshadow_audit.md"
        await asyncio.to_thread(audit_path.write_text, audit_text, encoding='utf-8')

        elapsed = time.time() - t_start
        self._emit_progress({"type": "complete", "elapsed": elapsed,
                             "message": f"全书脉络报告生成完成，耗时{elapsed:.0f}秒"})
        return final_report
