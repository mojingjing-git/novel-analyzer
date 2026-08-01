"""
写作风格分析器 — 代码统计 + LLM 语义提取
从原文 txt 文件提取硬指标，用 LLM 做 8 维语义风格提取。
（从旧项目 style_analyzer.py 移植 extract_style_profile 链路，LLM 调用改 async；
七维评分/style.md 独立工具不迁移——风格分析内置于最终总结流程）
"""
import re
import json
import logging
import random
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from backend.config.settings import APIConfig
from backend.core.llm_client import LLMClient

logger = logging.getLogger(__name__)

# ============================================================
# 中文文本处理工具
# ============================================================
CJK_CHAR = re.compile(r'[\u4e00-\u9fff]')
SENTENCE_END = re.compile(r'[。！？…]+')
PARA_SPLIT = re.compile(r'\n\s*\n|\n')

SENSORY_WORDS = [
    '闻到', '嗅到', '尝到', '触到', '摸到', '感到', '觉得', '觉着',
    '寒气', '冷气', '热气', '暖意', '凉意', '刺痛', '酸麻',
    '香气', '臭气', '腥味', '花香', '酒香',
    '瑟瑟', '萧萧', '飒飒', '簌簌', '潺潺', '嗡嗡',
    '月色', '星光', '火光', '灯光', '日光',
]
METAPHOR_WORDS = ['仿佛', '好像', '如同', '宛如', '犹如', '恰似', '好比', '似乎', '像似', '仿若']
PSYCHO_WORDS = ['心想', '暗想', '暗道', '心中', '心下', '忖道', '寻思', '自忖', '暗忖', '思忖',
                '心中暗', '心内', '腹中', '暗自', '心中想', '不由', '不禁', '忍不住']
COLLOQUIAL = ['咋', '啥', '咱', '俺', '嘛', '呗', '嘞', '啦', '吧', '呀', '啊', '呢', '吗']
LITERARY = ['之', '其', '乃', '遂', '即', '且', '而', '以', '于', '因', '故', '然', '虽', '若', '盖', '夫', '矣', '焉', '乎', '哉']
FOLLOW_MARKERS = ['只见', '但见', '忽见', '却说', '且说', '话说', '原来', '此乃', '那', '这日', '一日']
GOD_MARKERS = ['列位看官', '看官', '诸位', '且听', '按下不表', '闲言少叙', '诗曰', '有诗为证']
DIALOGUE_PATTERNS = [
    ("\u201c", "\u201d"), ("\u2018", "\u2019"),
    ("\u300c", "\u300d"), ("\u300e", "\u300f"),
    ('"', '"'),
]

TIME_WORDS = ['翌日', '次日', '当晚', '此时', '此刻', '片刻', '半晌',
              '须臾', '转眼', '随后', '不久', '俄顷', '少顷', '未几']


def count_cjk(text): return len(CJK_CHAR.findall(text))


def split_sentences(text):
    parts = SENTENCE_END.split(text)
    return [s.strip() for s in parts if s.strip() and count_cjk(s) > 2]


def extract_dialogues(text):
    dialogues = []
    for left, right in DIALOGUE_PATTERNS:
        pattern = re.escape(left) + r'([^' + right + r']{2,})' + re.escape(right)
        for m in re.finditer(pattern, text):
            dialogues.append(m.group(1))
    return dialogues


def word_density(text, words, total_chars):
    total = sum(text.count(w) for w in words)
    return total / max(total_chars / 1000, 1)


def count_markers(text, markers):
    return sum(text.count(m) for m in markers)


def _read_text_multi_encoding(f: Path) -> Optional[str]:
    """按常见中文编码依次尝试读取"""
    for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030']:
        try:
            return f.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return None


# ============================================================
# 统计分析（纯代码）
# ============================================================

def analyze_chapter_stats(text: str) -> Optional[Dict]:
    """单章硬指标统计"""
    total = count_cjk(text)
    if total < 20:
        return None

    sents = split_sentences(text)
    paras = [p.strip() for p in PARA_SPLIT.split(text) if p.strip() and count_cjk(p) > 5]
    dials = extract_dialogues(text)
    dial_chars = sum(count_cjk(d) for d in dials)

    sent_lens = [count_cjk(s) for s in sents]
    avg_sl = sum(sent_lens) / max(len(sent_lens), 1)
    std_sl = (sum((l - avg_sl) ** 2 for l in sent_lens) / max(len(sent_lens), 1)) ** 0.5
    short = len([l for l in sent_lens if l <= 10])
    long = len([l for l in sent_lens if l >= 50])
    ultra_short = len([l for l in sent_lens if 1 <= l <= 5])

    # 字符级 TTR（去重字符 / 总字符）
    cjk_chars = [c for c in text if CJK_CHAR.match(c)]
    ttr = len(set(cjk_chars)) / max(len(cjk_chars), 1)

    return {
        'total_chars': total,
        'sentence_count': len(sents),
        'paragraph_count': len(paras),
        'dialogue_ratio': dial_chars / max(total, 1),
        'avg_sentence_length': avg_sl,
        'short_sentence_ratio': short / max(len(sent_lens), 1),
        'sensory_density': word_density(text, SENSORY_WORDS, total),
        'metaphor_freq': word_density(text, METAPHOR_WORDS, total),
        'psychological_density': word_density(text, PSYCHO_WORDS, total),
        'colloquial_count': sum(text.count(w) for w in COLLOQUIAL),
        'literary_count': sum(text.count(w) for w in LITERARY),
        'follow_markers': count_markers(text, FOLLOW_MARKERS),
        'god_markers': count_markers(text, GOD_MARKERS),
        # 句法扩展
        'sentence_length_std': std_sl,
        'long_sentence_ratio': long / max(len(sent_lens), 1),
        'ultra_short_ratio': ultra_short / max(len(sent_lens), 1),
        # 标点扩展
        'dash_density': (text.count('——') + text.count('—')) / max(total / 1000, 1),
        'semicolon_density': text.count('；') / max(total / 1000, 1),
        'question_density': text.count('？') / max(total / 1000, 1),
        'period_density': text.count('。') / max(total / 1000, 1),
        'ttr': ttr,
        'time_word_density': word_density(text, TIME_WORDS, total),
    }


def aggregate_stats(results: List[Dict]) -> Dict:
    """汇总多章统计"""
    valid = [r for r in results if r is not None]
    if not valid:
        return {}
    n = len(valid)

    def avg(key): return sum(r[key] for r in valid) / n

    total_chars = sum(r['total_chars'] for r in valid)
    return {
        'chapter_count': n,
        'total_chars': total_chars,
        'avg_sentence_length': avg('avg_sentence_length'),
        'short_sentence_ratio': avg('short_sentence_ratio'),
        'dialogue_ratio': avg('dialogue_ratio'),
        'sensory_density': avg('sensory_density'),
        'metaphor_freq': avg('metaphor_freq'),
        'psychological_density': avg('psychological_density'),
        'literary_ratio': sum(r['literary_count'] for r in valid) / max(total_chars, 1) * 100,
        'colloquial_ratio': sum(r['colloquial_count'] for r in valid) / max(total_chars, 1) * 100,
        'follow_ratio': sum(r['follow_markers'] for r in valid) / max(sum(r['sentence_count'] for r in valid), 1),
        'god_ratio': sum(r['god_markers'] for r in valid) / max(total_chars / 1000, 1),
        'sentence_length_std': avg('sentence_length_std'),
        'long_sentence_ratio': avg('long_sentence_ratio'),
        'ultra_short_ratio': avg('ultra_short_ratio'),
        'dash_density': avg('dash_density'),
        'semicolon_density': avg('semicolon_density'),
        'question_density': avg('question_density'),
        'period_density': avg('period_density'),
        'ttr': avg('ttr'),
        'time_word_density': avg('time_word_density'),
    }


# ============================================================
# 章节采样
# ============================================================

def sample_chapters(blocks_dir: Path, n: int = 6) -> List[Tuple[int, str]]:
    """取 n 个代表性章节（前半随机3 + 后半随机3），每章随机位置取 1500 字"""
    rng = random.Random(42)  # 独立 RNG，不干扰全局种子
    files = sorted(blocks_dir.glob('*.txt'),
                   key=lambda f: int(re.match(r'(\d+)', f.stem).group(1)) if re.match(r'(\d+)', f.stem) else 0)
    if not files:
        return []

    total = len(files)
    half = total // 2
    first_half = list(range(0, half))
    second_half = list(range(half, total))
    picked_first = rng.sample(first_half, min(3, len(first_half)))
    picked_second = rng.sample(second_half, min(3, len(second_half)))
    indices = sorted(set(picked_first + picked_second))

    samples = []
    for idx in indices:
        f = files[idx]
        m = re.match(r'(\d+)', f.stem)
        ch_num = int(m.group(1)) if m else idx
        try:
            text = _read_text_multi_encoding(f)
            if text is None:
                continue
            # 随机位置取 1500 字（避免总是开头）
            cjk_len = count_cjk(text)
            if cjk_len > 1500:
                offset = rng.randint(0, cjk_len - 1500)
                chars = [c for c in text if CJK_CHAR.match(c)]
                text = ''.join(chars[offset:offset + 1500])
            samples.append((ch_num, text))
        except Exception:
            continue
    return samples


# ============================================================
# 语义风格提取（LLM）
# ============================================================

def build_semantic_prompt(agg: Dict, samples: List[Tuple[int, str]], book_name: str) -> str:
    """构建语义风格提取 prompt"""
    stats_text = json.dumps(agg, ensure_ascii=False, indent=2)

    chapter_samples = ""
    for ch_num, text in samples:
        sample = text[:1500]
        chapter_samples += f"\n\n### 第{ch_num}章片段\n{sample}"

    return f"""你是一位专业的中文小说写作风格分析师。请分析《{book_name}》的写作风格。

## 统计数据
{stats_text}

## 章节样本
{chapter_samples}

---

请基于以上统计数据和章节样本，从 8 个维度分析写作风格。每个维度用 1-2 句话描述，要求：
1. 每个结论必须附带原文例证或统计数据支撑
2. 用量化词（高/中/低、多/少）而非模糊词
3. 角色口癖需列出具体角色名和特征

输出纯 JSON（不要 Markdown 包裹）：

{{
  "signature_expressions": "高频句式和标志性表达...",
  "narrative_rhythm": "节奏控制习惯...",
  "dialogue_style": "对话风格特征...",
  "rhetorical_preferences": "修辞偏好和意象系统...",
  "emotional_expression": "情感表达方式...",
  "narrative_voice": "叙事视角特征...",
  "information_control": "信息释放策略...",
  "narrator_and_genre": "叙述者风格和类型混合..."
}}

直接输出 JSON，不要解释。"""


SEMANTIC_FIELDS = [
    'signature_expressions', 'narrative_rhythm', 'dialogue_style',
    'rhetorical_preferences', 'emotional_expression', 'narrative_voice',
    'information_control', 'narrator_and_genre'
]


def _validate_semantic_result(result: dict) -> dict:
    """验证语义提取结果，缺失字段用默认值填充"""
    for f in SEMANTIC_FIELDS:
        if f not in result or not isinstance(result[f], str):
            result[f] = '（未提取）'
    return result


async def call_llm_semantic(prompt: str, api_config: dict) -> Optional[dict]:
    """调用 LLM 获取语义风格特征（async）"""
    client = LLMClient(APIConfig(
        base_url=api_config['base_url'],
        api_key=api_config['api_key'],
        model=api_config['model'],
        max_tokens=20000,
        timeout=120,
        temperature=0.3,
        temperature_step=0.1,
        temperature_max_retries=2,
        backoff_max_retries=1,
    ))

    messages = [
        {"role": "system", "content": "你是专业的中文小说写作风格分析师。输出纯 JSON，不要 Markdown。"},
        {"role": "user", "content": prompt}
    ]
    success, content, error, tokens = await client.chat_with_retry(messages, max_tokens=20000)

    if not success:
        logger.warning(f"语义风格 LLM 调用失败: {error}")
        return None

    # 提取 JSON（剥离可能的代码块包裹，兜底截取花括号区间）
    content = content.strip()
    content = re.sub(r'^```(?:json)?\s*\n?', '', content)
    content = re.sub(r'\n?```\s*$', '', content)
    try:
        return _validate_semantic_result(json.loads(content))
    except json.JSONDecodeError:
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end > start:
            try:
                return _validate_semantic_result(json.loads(content[start:end + 1]))
            except json.JSONDecodeError:
                pass
    logger.warning("语义风格 JSON 解析失败")
    return None


def compute_book_stats(blocks_dir: Path) -> Tuple[Dict, List[Tuple[int, str]]]:
    """全书统计 + 采样（纯 CPU/IO，供 asyncio.to_thread 调用）"""
    samples = sample_chapters(blocks_dir, n=6)
    if not samples:
        return {}, []

    all_chapters = []
    for f in sorted(blocks_dir.glob('*.txt'),
                    key=lambda f: int(re.match(r'(\d+)', f.stem).group(1))
                    if re.match(r'(\d+)', f.stem) else 0):
        if not re.match(r'(\d+)', f.stem):
            continue
        try:
            text = _read_text_multi_encoding(f)
            if text is not None:
                all_chapters.append(text)
        except Exception:
            continue

    if not all_chapters:
        return {}, samples

    stats = [analyze_chapter_stats(text) for text in all_chapters]
    return aggregate_stats(stats), samples


async def extract_style_profile(blocks_dir: Path, book_name: str, api_config: dict) -> Optional[dict]:
    """
    提取风格 profile JSON（供 FinalSummaryRunner 注入 prompt）。
    统计部分走线程池，语义部分 async LLM。
    """
    import asyncio

    agg, samples = await asyncio.to_thread(compute_book_stats, blocks_dir)
    if not samples:
        logger.warning("风格分析：无可用章节样本")
        return None
    if not agg:
        logger.warning("风格分析：无有效统计数据")
        return None

    semantic_prompt = build_semantic_prompt(agg, samples, book_name)
    logger.info(f"语义风格提取中 (样本{len(samples)}章, ~{len(semantic_prompt)}字符)...")
    semantic = await call_llm_semantic(semantic_prompt, api_config)
    if semantic is None:
        logger.warning("语义风格提取失败，返回默认值")
        semantic = {f: '（提取失败）' for f in SEMANTIC_FIELDS}

    style_profile = {
        "statistical": agg,
        "semantic": semantic,
        "sample_chapters": [ch for ch, _ in samples],
    }
    logger.info(f"风格提取完成: {agg['chapter_count']}章统计 + 8维语义")
    return style_profile


SEMANTIC_FIELD_LABELS = {
    'signature_expressions': '标志性表达',
    'narrative_rhythm': '叙事节奏',
    'dialogue_style': '对话风格',
    'rhetorical_preferences': '修辞偏好',
    'emotional_expression': '情感表达',
    'narrative_voice': '叙事视角',
    'information_control': '信息释放',
    'narrator_and_genre': '叙述者/类型',
}


async def analyze_book(blocks_dir: Path, book_name: str, use_llm: bool = True,
                       limit: int = 50) -> Optional[str]:
    """
    分析整本书的写作风格，返回 Markdown 报告字符串。
    纯统计部分走线程池；LLM 语义部分可选（use_llm）。
    （style_service 旧代码曾错误地从本模块导入一个并不存在的 analyze_book，
     现已在此补齐，点击「开始风格分析」不再 ImportError。）
    """
    import asyncio
    from ..config.settings import ConfigManager

    lines: List[str] = [f"# 《{book_name}》写作风格分析", ""]
    agg: Dict = {}
    samples: List[Tuple[int, str]] = []
    semantic: Optional[Dict] = None

    if use_llm:
        # 必须 load() 才会从 config.json 读取真实配置（base_url/api_key/model），
        # 否则 ConfigManager().config 永远是默认配置（空 key/空 model）
        cfg = ConfigManager().load()
        api_config = {
            "base_url": cfg.api.base_url,
            "api_key": cfg.api.api_key,
            "model": cfg.api.model,
        }
        profile = await extract_style_profile(blocks_dir, book_name, api_config)
        if profile:
            agg = profile.get("statistical", {}) or {}
            semantic = profile.get("semantic")
            samples = profile.get("sample_chapters", []) or []
    else:
        agg, samples = await asyncio.to_thread(compute_book_stats, blocks_dir)

    if not agg and not samples:
        logger.warning("风格分析：无有效章节数据")
        return None

    # 量化统计
    lines.append("## 一、量化统计")
    lines.append("")
    if agg:
        for k, v in agg.items():
            lines.append(f"- **{k}**: {v}")
    else:
        lines.append("- （无统计数据）")
    lines.append("")

    # 语义风格（LLM）
    if semantic:
        lines.append("## 二、语义风格（LLM 提取）")
        lines.append("")
        for f in SEMANTIC_FIELDS:
            val = semantic.get(f, '（未提取）')
            label = SEMANTIC_FIELD_LABELS.get(f, f)
            lines.append(f"- **{label}**: {val}")
        lines.append("")
        if samples:
            chs = ", ".join(f"第{ch}章" for ch in samples)
            lines.append(f"> 采样章节：{chs}")
            lines.append("")

    return "\n".join(lines)
