"""
切分工具服务 —— 功能增强版

将旧项目 cuttttttt_pyqt6.py 的切分逻辑，与两个开源实现里最实用的部分合并重写：
  * zzk6780051/novel  (.github/scripts/fenli.py)
        - 多格式章节正则（中文/阿拉伯数字、卷章节回、英文 Chapter、括号包裹）
        - 按前 N 行对每个候选正则打分，自动选出本书最优正则
        - 从正文前若干行提取书名/作者等元数据
        - 基于内容 MD5 的相邻去重
  * oomol-lab/txt-to-epub-converter  (src/txt_to_epub)
        - 卷/章/节 层级结构识别（volume / chapter / section）
        - 章节最小 / 最大字数限制

本模块在保留「workspace/{书名}/blocks/{index}.txt 扁平数字命名」契约的前提下，
新增：卷层级分组、番外/序章/楔子/尾声等特章、中文数字转阿拉伯、广告/水印行清理、
最小字数跳过或合并短章、超大章自动拆分、相邻去重、书名作者推断、统计信息。

输出：
  blocks/{index:04d}.txt        每章正文（标题 + 换行 + 正文）
  blocks/chapters.json          结构化章节清单（含卷、章节号、字数）
  blocks/metadata.json          推断出的书名 / 作者
  blocks/split_report.txt       人类可读切分报告
"""

import json
import logging
import os
import re
import shutil
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional

from backend.utils.text_utils import detect_and_decode
from backend.config.constants import ENCODING_CANDIDATES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 中文数字 → 阿拉伯数字
# ---------------------------------------------------------------------------
_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000,
}


def cn_to_int(chars: str) -> Optional[int]:
    """把『一百二十三』『万』『两』等中文数字转换为 int；纯阿拉伯直接转。"""
    chars = chars.strip()
    if not chars:
        return None
    if chars.isdigit():
        return int(chars)
    # 过滤掉非数字汉字
    if not any(c in _CN_NUM for c in chars):
        return None
    total = 0      # 已结算部分（跨越 万/亿 边界后累计）
    section = 0    # 当前节内（万以下）累计
    number = 0     # 当前正在拼接的小于 十 的数
    for ch in chars:
        if ch in ("零", "〇"):
            continue
        elif ch in "一二三四五六七八九两":
            number = number * 10 + _CN_NUM[ch]
        elif ch == "十":
            number = 10 if number == 0 else number * 10
            section += number
            number = 0
        elif ch == "百":
            number = 1 if number == 0 else number
            section += number * 100
            number = 0
        elif ch == "千":
            number = 1 if number == 0 else number
            section += number * 1000
            number = 0
        elif ch == "万":
            # 结算进 total，重置 section——旧实现直接改写 section 导致
            # "一亿零二万三千" 被算成 1000000023000（亿级联乘重复累计）
            total += (section + number) * 10000
            section = 0
            number = 0
        elif ch == "亿":
            total += (section + number) * 100000000
            section = 0
            number = 0
    return total + section + number


# ---------------------------------------------------------------------------
# 候选章节 / 卷 正则库（用于「自动检测」模式打分）
# ---------------------------------------------------------------------------
# 章节（章 / 回 / 节 / 特章 / 英文 / 数字编号）
CHAPTER_PATTERNS: List[tuple] = [
    ("第X章(中文)", r"^第[零一二三四五六七八九十百千两万千亿\d]+章"),
    ("第X章(阿拉伯)", r"^第\d+章"),
    ("第X回", r"^第[零一二三四五六七八九十百千两万千亿\d]+回"),
    ("第X节", r"^第[零一二三四五六七八九十百千两万千亿\d]+节"),
    ("卷X第X章", r"^[上下中下零一二三四五六七八九十百千两万千亿\d]+卷.*第[零一二三四五六七八九十百千两万千亿\d]+[章节回]"),
    ("第一卷第一章", r"^第[零一二三四五六七八九十百千两万千亿\d]+卷\s*第[零一二三四五六七八九十百千两万千亿\d]+[章节回]"),
    ("卷一第一章", r"^[卷部篇][零一二三四五六七八九十百千两万千亿\d]+卷?\s*第[零一二三四五六七八九十百千两万千亿\d]+[章节回]"),
    ("英文Chapter", r"^chapter\s+\d+"),
    ("数字点编号", r"^\d+[\.、]\s*\S"),
    ("中文数字顿号", r"^[零一二三四五六七八九十百千]+、"),
    ("括号章", r"^[\【\[]第[零一二三四五六七八九十百千两万千亿\d]+[章节回]"),
    ("特章(序/楔/番外/尾声…)", r"^(序章|序言|引子|引言|前言|楔子|后记|后序|尾声|终章|终幕|番外|外传|附录|彩蛋|特别篇|完本感言)"),
]

# 卷 / 部 / 篇 标记
VOLUME_PATTERNS: List[tuple] = [
    ("第X卷", r"^第[零一二三四五六七八九十百千两万千亿\d]+[卷部篇]"),
    ("卷X", r"^[卷部篇][零一二三四五六七八九十百千两万千亿\d]+"),
    ("上下卷", r"^[上下中]+[卷部篇]"),
    ("英文卷", r"^(volume|book|part)\s+[\dIVXLC]+"),
]

# 兼容旧代码的单一默认正则（作为「自定义」模式兜底）
DEFAULT_CHAPTER_PATTERN = (
    r"^(?:第[零一二三四五六七八九十百千万0-9]+[章回节]|"
    r"第[0-9]+[章回节]|[0-9]+[、\.]|[0-9]+\.[0-9]+|"
    r"卷[零一二三四五六七八九十百千万0-9]+|"
    r"[零一二三四五六七八九十百千万0-9]+[卷]|"
    r"第[零一二三四五六七八九十百千万0-9]+[部分]|"
    r"第[0-9]+[部分]|[0-9]+[集幕])[^\n]*"
)

# 常见广告 / 水印行（清理开关开启时剔除）
# 分强弱两级：强标记几乎不可能出现在正文，命中即删；
# 弱标记（微信/下载/月票等）在正文中高频出现，需配合"短行无标点"才删（见 _is_ad_line）
_AD_STRONG = [
    "求月票", "求收藏", "求推荐", "求订阅", "求票", "月票推荐票", "求评价票",
    "求鲜花", "投喂", "催更", "上架感言", "作者感言", "完本感言", "书友群",
    "读者群", "qq群", "q群", "公众号", "作者新书", "笔趣阁", "八一中文",
    "本章说", "防盗章", "防复制", "无弹窗", "最新章节", "章节错误", "请报告",
    "手机阅读", "本书首发", "更新最快", "请收藏", "天才一秒记住", "一秒记住",
    "txt下载", "全文阅读", "顶点小说", "思路客",
]
_AD_WEAK = [
    "月票", "推荐票", "打赏", "赏金", "感言", "微信", "下载", "新书",
    "ps：", "ps:", "p.s", "enjoyment",
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ChapterBlock:
    index: int
    title: str
    content: str
    word_count: int
    volume: Optional[str] = None
    volume_index: int = 0
    num: Optional[int] = None


@dataclass
class SplitResult:
    chapters: List[ChapterBlock] = field(default_factory=list)
    total_chapters: int = 0
    total_words: int = 0
    total_volumes: int = 0
    dedup_count: int = 0
    detected_pattern: str = ""
    pattern_name: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SplitOptions:
    pattern: str = ""          # 自定义正则（mode=custom 时生效）
    mode: str = "auto"         # auto | custom
    use_volume: bool = True    # 是否识别卷/部/篇层级
    min_words: int = 0         # 小于该字数的章：跳过（merge_tiny=False）或合并（True）
    merge_tiny: bool = False    # 短章合并到下一章而非丢弃
    max_words: int = 0         # 大于该字数的章：自动拆分为多块（0=不拆分）
    remove_ads: bool = False   # 清理广告 / 水印行
    strip_whitespace: bool = True
    scan_lines: int = 800      # 自动检测时扫描的前 N 行


# ---------------------------------------------------------------------------
# 编码 / 读取
# ---------------------------------------------------------------------------
# 与其他入口（file_processor / routes_prompt 等）共用 ENCODING_CANDIDATES 常量，
# 包含 gb18030（GBK 超集，能解 GBK 边界字符）和 latin-1 兜底。
# P3 修复（2026-08-27）：之前的本地列表缺 gb18030，导致 4.5MB GBK 小说在
# 256KB 采样边界切到 2-byte 字符尾字节时，gbk 解码失败，splitter 路径
# 抛 "无法使用任何编码读取文件"。与 file_processor.py 行为对齐后，splitter
# 与 file_processor 选编码结果一致。
def _detect_and_read(file_path: Path) -> str:
    content = detect_and_decode(file_path, ENCODING_CANDIDATES)
    if content.startswith("\ufeff"):
        content = content[1:]
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    return content


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _extract_chapter_num(title: str) -> Optional[int]:
    """从标题里提取章节号（阿拉伯或中文），没有则返回 None。"""
    m = re.search(r"第\s*([0-9零一二三四五六七八九十百千两万千亿]+)\s*[章回节]", title)
    if m:
        return cn_to_int(m.group(1))
    m = re.search(r"chapter\s+(\d+)", title, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.match(r"^\s*([0-9]+)[\.、]", title)
    if m:
        return int(m.group(1))
    return None


def _compile_volume_patterns() -> List[re.Pattern]:
    return [re.compile(p, re.MULTILINE) for _, p in VOLUME_PATTERNS]


def _normalize_volume_label(line: str) -> Optional[str]:
    """从『第一卷 风起青萍』『第一卷第一章』『卷二』『Part I』等行里提取规范卷标签。

    统一输出形如「第X卷 / 卷X / 上卷 / Part I」，保证『独立卷行』与
    『第X卷第Y章』合并写法被归到同一个卷标签下。
    """
    m = re.match(r"^(?:第)?\s*([零一二三四五六七八九十百千两万千亿\d]+)\s*([卷部篇])", line, re.IGNORECASE)
    if m:
        return f"第{m.group(1)}{m.group(2)}"
    m = re.match(r"^(卷|部|篇)\s*([零一二三四五六七八九十百千两万千亿\d]+)", line, re.IGNORECASE)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m = re.match(r"^([上下中])\s*([卷部篇])", line, re.IGNORECASE)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m = re.match(r"^(volume|book|part)\s+([\dIVXLC]+)", line, re.IGNORECASE)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return None


def _is_volume_start(line: str) -> Optional[str]:
    # 保守排除："中篇小说连载中/上篇简介" 等下载站前奏行不是卷标记，
    # 但会命中 "^[上下中]+[卷部篇]"（"中篇"），导致卷归属写错
    if "小说" in line or "连载" in line:
        return None
    for _, p in VOLUME_PATTERNS:
        if re.match(p, line, re.IGNORECASE):
            return _normalize_volume_label(line) or line.strip()
    return None


def _chapter_regex_for_mode(options: SplitOptions, lines: List[str]) -> tuple:
    """返回 (compiled_regex, pattern_name)。

    - pattern_name：扫描前 N 行打分选出的「主导格式」（仅用于展示）。
    - compiled_regex：合并【所有】候选章节正则的主正则，确保 章/回/节/序章/楔子/
      番外/尾声/英文 Chapter/数字编号 等任意形态都能被识别（功能最全）。
    """
    if options.mode == "custom" and options.pattern and options.pattern.strip():
        try:
            return re.compile(options.pattern, re.MULTILINE), "自定义正则"
        except re.error:
            logger.warning("自定义正则编译失败，回退自动检测")

    # 自动检测：对每个候选正则在前 N 行打分，选出主导格式用于展示
    scores: Dict[str, int] = {}
    for name, pat in CHAPTER_PATTERNS:
        try:
            rx = re.compile(pat, re.MULTILINE | re.IGNORECASE)
        except re.error:
            continue
        score = 0
        for line in lines[: options.scan_lines]:
            s = line.strip()
            if not s or len(s) > 60:
                continue
            if rx.match(s):
                score += 1
        scores[name] = score

    best_name = max(scores, key=lambda k: scores[k]) if scores else ""
    pattern_name = best_name if best_name and scores[best_name] > 0 else "兜底第X章"

    # 特章正则（序章/楔子/番外/尾声…）：无论主导格式如何都保留识别能力
    special_patterns = [pat for name, pat in CHAPTER_PATTERNS if name.startswith("特章")]
    special_alt = "|".join(special_patterns)

    # P2-11 + G6 收口（2026-08-24）：主导格式足够强时收窄为
    # 「主导 + 特章 + 达标次级」。次级入选双门槛：绝对得分 ≥ 2（排掉孤例噪声）
    # 且得分 ≥ 主导的 15%（排掉数量远超主导的高频噪声列表——那正是 P2-11 要防的
    # 过度切分场景）。合法卷章混排与主导同量级，必然入选。
    if best_name and scores[best_name] >= 5 and not best_name.startswith("特章"):
        _pat_by_name = {name: pat for name, pat in CHAPTER_PATTERNS}
        best_score = scores[best_name]
        combined = f"(?:{_pat_by_name[best_name]})"
        if special_alt:
            combined += f"|(?:{special_alt})"
        included_secondary = 0
        threshold = max(2, int(best_score * 0.15))
        for name, pat in CHAPTER_PATTERNS:
            if name == best_name or name.startswith("特章"):
                continue
            sc = scores.get(name, 0)
            if sc >= threshold:
                combined += f"|(?:{pat})"
                included_secondary += 1
        if included_secondary:
            logger.info(f"次级章节格式并入切分: {included_secondary} 种 "
                        f"(阈值≥{threshold})")
        return re.compile(combined, re.MULTILINE | re.IGNORECASE), pattern_name

    # 兜底：主导格式得分不足（前 N 行样本太少/无主导格式）时回退全量合并大网
    combined = "(" + ")|(".join(p for _, p in CHAPTER_PATTERNS) + ")"
    master = re.compile(combined, re.MULTILINE | re.IGNORECASE)
    return master, pattern_name


def _extract_metadata(lines: List[str], filename: str) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    for i, raw in enumerate(lines[:60]):
        line = raw.strip()
        if not line or len(line) > 120:
            continue
        # 书名：含《》的行，或前 8 行内且没有章节关键字的短行
        if not meta.get("title"):
            m = re.search(r"[《]([^》]{1,40})[》]", line)
            if m:
                meta["title"] = m.group(1)
            elif i < 8 and not any(k in line for k in ("第", "章", "回", "卷", "Chapter")):
                meta["title"] = line
        # 作者
        if not meta.get("author"):
            m = re.search(r"(?:作者|著)\s*[:：]?\s*(.+)", line)
            if m:
                meta["author"] = m.group(1).strip()
    if not meta.get("title"):
        meta["title"] = filename
    return meta


# ---------------------------------------------------------------------------
# 核心切分
# ---------------------------------------------------------------------------
def split_text(content: str, options: Optional[SplitOptions] = None) -> SplitResult:
    if options is None:
        options = SplitOptions()
    lines = content.split("\n")

    chapter_rx, pattern_name = _chapter_regex_for_mode(options, lines)
    volume_rxs = _compile_volume_patterns() if options.use_volume else []

    # 第一遍：定位所有「章/卷」起始行
    raw_blocks: List[Dict[str, Any]] = []
    current_volume: Optional[str] = None
    current_volume_index = 0
    preamble_lines: List[str] = []

    for idx, raw in enumerate(lines):
        s = raw.strip()
        # 卷标记
        if volume_rxs:
            vol = _is_volume_start(s)
            if vol:
                # 仅当该行不像章节时才作为卷标记（卷行不会以『章/回/节』结尾）
                if not re.search(r"[章回节]$", s):
                    current_volume = vol
                    current_volume_index += 1
                    continue
        # 章节标记（含『第X卷第Y章』合并写法，本行同时带卷号）
        if chapter_rx.match(s) and 0 < len(s) <= 80:
            # 合并写法：本行开头即卷号，更新当前卷（与独立卷行共用同一卷标签）
            vol_label = _normalize_volume_label(s)
            if vol_label and vol_label != current_volume:
                current_volume = vol_label
                current_volume_index += 1
            title = s
            raw_blocks.append({
                "title": title,
                "start": idx + 1,            # 下一行开始
                "volume": current_volume,
                "volume_index": current_volume_index,
            })

    if not raw_blocks:
        # 无法识别：整体作为单章
        text = content.strip() if options.strip_whitespace else content
        block = ChapterBlock(1, "全文", text, len(text))
        return SplitResult(
            chapters=[block], total_chapters=1, total_words=len(text),
            metadata=_extract_metadata(lines, ""),
            detected_pattern=chapter_rx.pattern, pattern_name=pattern_name,
        )

    # 第二遍：切出每块正文
    blocks: List[ChapterBlock] = []
    for i, b in enumerate(raw_blocks):
        end = raw_blocks[i + 1]["start"] - 1 if i + 1 < len(raw_blocks) else len(lines)
        body_lines = lines[b["start"]:end]
        if options.remove_ads:
            body_lines = [ln for ln in body_lines if not _is_ad_line(ln)]
        body = "\n".join(body_lines)
        if options.strip_whitespace:
            body = body.strip()
        title = b["title"]
        num = _extract_chapter_num(title)
        block = ChapterBlock(
            index=0,
            title=title,
            content=(title + "\n" + body) if body else title,
            word_count=len(body),
            volume=b["volume"],
            volume_index=b["volume_index"],
            num=num,
        )
        blocks.append(block)

    # 正文开头（第一个章节之前的内容）拼到首章
    if raw_blocks and raw_blocks[0]["start"] > 0:
        pre = "\n".join(lines[: raw_blocks[0]["start"]])
        if options.remove_ads:
            pre_lines = [ln for ln in pre.split("\n") if not _is_ad_line(ln)]
            pre = "\n".join(pre_lines)
        pre = pre.strip() if options.strip_whitespace else pre
        if pre:
            first = blocks[0]
            first.content = pre + "\n\n" + first.content
            first.word_count = len(first.content) - len(first.title)

    # 相邻去重（常见于防盗章 / 重复章）
    deduped: List[ChapterBlock] = []
    prev_hash: Optional[str] = None
    dedup_count = 0
    for blk in blocks:
        h = hashlib.md5(blk.content.encode("utf-8")).hexdigest()
        if h == prev_hash:
            dedup_count += 1
            continue
        prev_hash = h
        deduped.append(blk)
    blocks = deduped

    # 最小字数：跳过 or 合并短章
    if options.min_words > 0:
        if options.merge_tiny:
            blocks = _merge_tiny(blocks, options.min_words)
        else:
            blocks = [b for b in blocks if b.word_count >= options.min_words or b.num is None]

    # 超大章自动拆分
    if options.max_words > 0:
        blocks = _split_oversized(blocks, options.max_words)

    # 重新编号
    for i, b in enumerate(blocks, start=1):
        b.index = i

    # 统计
    counts = [b.word_count for b in blocks] or [0]
    counts_sorted = sorted(counts)
    stats = {
        "avg": sum(counts) // len(counts),
        "min": min(counts),
        "max": max(counts),
        "median": counts_sorted[len(counts_sorted) // 2],
    }
    volumes = sorted({b.volume for b in blocks if b.volume})
    metadata = _extract_metadata(lines, "")

    return SplitResult(
        chapters=blocks,
        total_chapters=len(blocks),
        total_words=sum(counts),
        total_volumes=len(volumes),
        dedup_count=dedup_count,
        detected_pattern=chapter_rx.pattern,
        pattern_name=pattern_name,
        metadata=metadata,
        stats=stats,
    )


def _is_ad_line(line: str) -> bool:
    s = line.strip().lower()
    if not s:
        return False
    if len(s) > 200:
        return False
    # 强广告标记：命中即删（公众号/书友群/笔趣阁 等几乎不可能出现在正文）
    if any(k in s for k in _AD_STRONG):
        return True
    # 弱标记（微信/下载/月票/推荐票 等会出现在正文里）：
    # 仅当"短行 + 无句子标点"才视为广告，避免误删"他打开微信看到消息"这类正文行
    if any(k in s for k in _AD_WEAK):
        return len(s) <= 40 and not any(p in s for p in "，。！？；：,;!?")
    return False


def _merge_tiny(blocks: List[ChapterBlock], min_words: int) -> List[ChapterBlock]:
    """把短于 min_words 的章合并进下一章（末尾则并入上一章）。"""
    merged: List[ChapterBlock] = []
    for blk in blocks:
        if blk.word_count < min_words and blk.num is not None:
            if merged:
                prev = merged[-1]
                prev.content += "\n\n" + blk.content
                prev.word_count = len(prev.content) - len(prev.title)
            else:
                merged.append(blk)
        else:
            merged.append(blk)
    # 末尾短章并入上一章
    if len(merged) >= 2 and merged[-1].word_count < min_words:
        last = merged.pop()
        merged[-1].content += "\n\n" + last.content
        merged[-1].word_count = len(merged[-1].content) - len(merged[-1].title)
    return merged


def _split_oversized(blocks: List[ChapterBlock], max_words: int) -> List[ChapterBlock]:
    """把超过 max_words 的章按段落拆成多块，标题加 (n/m) 后缀。

    标题固定保留在第 1 块；若某个段落本身超过 max_words，则按字符硬切。
    """
    out: List[ChapterBlock] = []
    for blk in blocks:
        if blk.word_count <= max_words:
            out.append(blk)
            continue
        parts = blk.content.split("\n", 1)
        title_line = parts[0]
        body = parts[1] if len(parts) > 1 else ""
        paragraphs = body.split("\n") if body else [""]

        # 超长单段落按字符硬切
        norm_paras: List[str] = []
        for p in paragraphs:
            if len(p) > max_words:
                for i in range(0, len(p), max_words):
                    norm_paras.append(p[i:i + max_words])
            else:
                norm_paras.append(p)

        # 按 max_words 累积成块
        chunks: List[List[str]] = [[]]
        size = 0
        for p in norm_paras:
            if size + len(p) + 1 > max_words and chunks[-1]:
                chunks.append([])
                size = 0
            chunks[-1].append(p)
            size += len(p) + 1

        # 末块过小则并入上一块，避免无意义的碎片续章
        if len(chunks) > 1:
            last_len = sum(len(p) for p in chunks[-1])
            if last_len < max(200, max_words // 10):
                chunks[-2].extend(chunks[-1])
                chunks.pop()

        total = len(chunks)
        for i, ch in enumerate(chunks, start=1):
            suffix = f" ({i}/{total})" if total > 1 else ""
            text = (title_line + suffix + "\n" + "\n".join(ch)) if ch else (title_line + suffix)
            out.append(ChapterBlock(
                index=0,
                title=blk.title + suffix,
                content=text,
                word_count=len(text),
                volume=blk.volume,
                volume_index=blk.volume_index,
                num=blk.num,
            ))
    return out


# ---------------------------------------------------------------------------
# 对外接口（保持旧签名 + 新增 options）
# ---------------------------------------------------------------------------
def preview_split(
    file_path: Path,
    options: Optional[SplitOptions] = None,
    preview_count: int = 50,
) -> Dict[str, Any]:
    if options is None:
        options = SplitOptions()
    content = _detect_and_read(file_path)
    result = split_text(content, options)
    preview_blocks = result.chapters[:preview_count]
    return {
        "chapters": [
            {
                "index": b.index,
                "title": b.title,
                "word_count": b.word_count,
                "volume": b.volume,
                "num": b.num,
            }
            for b in preview_blocks
        ],
        "total_chapters": result.total_chapters,
        "total_words": result.total_words,
        "total_volumes": result.total_volumes,
        "dedup_count": result.dedup_count,
        "detected_pattern": result.detected_pattern,
        "pattern_name": result.pattern_name,
        "metadata": result.metadata,
        "stats": result.stats,
        "preview_count": len(preview_blocks),
    }


def save_split(
    file_path: Path,
    output_dir: Path,
    options: Optional[SplitOptions] = None,
) -> Dict[str, Any]:
    if options is None:
        options = SplitOptions()
    content = _detect_and_read(file_path)
    result = split_text(content, options)
    output_dir.mkdir(parents=True, exist_ok=True)

    # P1 修复（2026-08-24）：先全部暂存到 _split_staging，全部成功后才清理旧章并交换。
    # 原实现先删旧 txt 再并行写新文件，中途任一写入失败会留下
    # 「旧章已删、新章残缺、chapters.json 还指向已删除文件」的不一致状态。
    chapters = result.chapters
    if chapters:
        staging_dir = output_dir / "_split_staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True)
        try:
            max_workers = min(32, (os.cpu_count() or 4) + 4)

            def _stage(b):
                (staging_dir / f"{b.index:04d}.txt").write_text(b.content, encoding="utf-8")

            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                list(ex.map(_stage, chapters))
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise  # 旧 blocks 一个都没动过

        # 暂存全部成功后才进入破坏性阶段：清理旧章（含网络盘延迟优化注释保留）
        for stale in output_dir.glob("*.txt"):
            stem = stale.name[:-4]
            if stem.isdigit():
                try:
                    stale.unlink()
                except OSError:
                    pass
        for staged in sorted(staging_dir.iterdir()):
            os.replace(staged, output_dir / staged.name)
        shutil.rmtree(staging_dir, ignore_errors=True)

    # 结构化章节清单
    chapters_json = [
        {
            "index": b.index,
            "title": b.title,
            "word_count": b.word_count,
            "volume": b.volume,
            "num": b.num,
        }
        for b in result.chapters
    ]
    (output_dir / "chapters.json").write_text(
        json.dumps(chapters_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "metadata.json").write_text(
        json.dumps(result.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 人类可读报告
    lines_report = [
        f"书名: {result.metadata.get('title', '')}",
        f"作者: {result.metadata.get('author', '')}",
        f"检测正则: {result.pattern_name}  ({result.detected_pattern})",
        f"总章节数: {result.total_chapters}",
        f"总卷数: {result.total_volumes}",
        f"总字数: {result.total_words}",
        f"去重章节数: {result.dedup_count}",
        f"字数统计: 平均 {result.stats.get('avg')} / 最小 {result.stats.get('min')} / 最大 {result.stats.get('max')} / 中位 {result.stats.get('median')}",
        "章节列表:",
    ]
    for b in result.chapters:
        vol = f"[{b.volume}] " if b.volume else ""
        lines_report.append(f"{b.index:04d}. {vol}{b.title} ({b.word_count} 字)")
    (output_dir / "split_report.txt").write_text(
        "\n".join(lines_report), encoding="utf-8"
    )

    logger.info(f"切分完成: {output_dir} ({result.total_chapters} 章)")
    return {
        "saved": True,
        "output_dir": output_dir.as_posix(),
        "total_chapters": result.total_chapters,
        "total_words": result.total_words,
        "total_volumes": result.total_volumes,
        "dedup_count": result.dedup_count,
    }


def infer_book_name(file_path: Path) -> str:
    """从文件名推断书名（去除常见后缀）"""
    name = file_path.stem
    for suffix in ["（校对版全本）", "(校对版全本)", "（全本）", "(全本)",
                   "（校对版）", "(校对版)", "（完整版）", "(完整版)",
                   "_分析报告", "_分析", "-分析报告"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.strip()


def save_to_workspace(
    file_path: Path,
    workspace_dir: Path,
    book_name: str,
    options: Optional[SplitOptions] = None,
) -> Dict[str, Any]:
    """切分并保存到工作区 workspace/{书名}/blocks/"""
    book_dir = workspace_dir / book_name
    blocks_dir = book_dir / "blocks"
    result = save_split(file_path, blocks_dir, options)
    result["book_name"] = book_name
    result["workspace_dir"] = book_dir.as_posix()
    return result
