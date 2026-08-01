"""
文本处理工具
提供编码检测、文本截断等功能
（自旧项目 utils/text_utils.py 原样移植）
"""

from pathlib import Path
from typing import List


def detect_and_decode(file_path: Path, encoding_priority: List[str]) -> str:
    """
    按优先级尝试多种编码读取文件

    Args:
        file_path: 文件路径
        encoding_priority: 编码优先级列表

    Returns:
        解码后的文本内容

    Raises:
        UnicodeDecodeError: 所有编码都失败时抛出
    """
    last_error = None

    for encoding in encoding_priority:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
            return content
        except UnicodeDecodeError as e:
            last_error = e
            continue
        except Exception as e:
            raise IOError(f"读取文件失败: {e}")

    raise last_error or UnicodeDecodeError(
        "unknown", b"", 0, 1,
        f"无法使用任何编码读取文件: {file_path}"
    )


def detect_encoding(file_path: Path, encoding_priority: List[str]) -> str:
    """
    检测文件编码，返回第一个成功的编码名称

    Args:
        file_path: 文件路径
        encoding_priority: 编码优先级列表

    Returns:
        成功的编码名称

    Raises:
        UnicodeDecodeError: 所有编码都失败时抛出
    """
    last_error = None

    for encoding in encoding_priority:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                f.read()
            return encoding
        except UnicodeDecodeError as e:
            last_error = e
            continue
        except Exception as e:
            raise IOError(f"读取文件失败: {e}")

    raise last_error or UnicodeDecodeError(
        "unknown", b"", 0, 1,
        f"无法使用任何编码读取文件: {file_path}"
    )


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    截断文本到指定长度

    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后缀

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    if max_length <= len(suffix):
        return suffix[:max_length]
    return text[:max_length - len(suffix)] + suffix


def clean_text(text: str) -> str:
    """
    清理文本（去除多余空白）

    Args:
        text: 原始文本

    Returns:
        清理后的文本
    """
    import re
    # 合并多个空白字符
    text = re.sub(r'\s+', ' ', text)
    # 去除首尾空白
    return text.strip()

import difflib as _difflib
import re as _re


def is_text_duplicate(new_text: str, existing_set: set, threshold: float = 0.75) -> bool:
    """
    检查新文本是否与已有集合中的某条语义重复。
    三重判断：归一化相同 / 子串包含 / SequenceMatcher 相似度。

    Args:
        new_text: 待检查的文本
        existing_set: 已有文本集合
        threshold: SequenceMatcher 相似度阈值（默认 0.75）

    Returns:
        True 表示疑似重复
    """
    new_norm = _normalize_for_dedup(new_text)
    if not new_norm:
        return False
    for exist in existing_set:
        exist_norm = _normalize_for_dedup(exist)
        # 1. 归一化后完全相同
        if new_norm == exist_norm:
            return True
        # 2. 一个是另一个的子串（两者长度都 > 3）
        if len(new_norm) > 3 and len(exist_norm) > 3:
            if new_norm in exist_norm or exist_norm in new_norm:
                return True
        # 3. 序列相似度（考虑字符顺序）
        ratio = _difflib.SequenceMatcher(None, new_norm, exist_norm).ratio()
        if ratio >= threshold:
            return True
    return False


def _normalize_for_dedup(text: str) -> str:
    """去除虚词和标点，用于去重比较"""
    stopwords = set('的与和或在中是了被对为从到')
    return ''.join(
        c for c in text
        if c not in stopwords and not _re.match(r'[^\w\u4e00-\u9fff]', c)
    )


def _extract_keywords(text: str, min_len: int = 2, max_len: int = 4) -> set:
    """
    从中文文本中提取 2-4 字关键词（用于伏笔去重的粗筛）

    Args:
        text: 输入文本
        min_len: 关键词最小长度
        max_len: 关键词最大长度

    Returns:
        关键词集合
    """
    stopwords = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
                 '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去',
                 '你', '会', '着', '没有', '看', '好', '自己', '这', '他', '她',
                 '它', '们', '那', '被', '把', '从', '对', '与', '或', '但',
                 '而', '了', '吗', '呢', '吧', '啊', '其', '中', '为', '以',
                 '及', '等', '此', '些', '于', '由', '该', '则', '如', '所'}

    # 提取连续中文字符片段
    segments = _re.findall(r'[\u4e00-\u9fff]+', text)
    keywords = set()
    for seg in segments:
        for length in range(min_len, min(max_len + 1, len(seg) + 1)):
            for i in range(len(seg) - length + 1):
                word = seg[i:i + length]
                if word not in stopwords:
                    keywords.add(word)
    return keywords


def deduplicate_foreshadows(
    clues: list,
    keyword_overlap_min: int = 2,
    similarity_threshold: float = 0.6,
) -> list:
    """
    伏笔线索去重：从所有章节的 foreshadowing clue 中提取唯一伏笔。

    两阶段去重策略：
    1. 关键词交集粗筛：两条 clue 的关键词交集 >= keyword_overlap_min -> 候选重复对
    2. SequenceMatcher 精筛：候选对相似度 >= similarity_threshold -> 确认重复

    Args:
        clues: [(chapter, clue_text) 或 (chapter, clue_text, type, confidence), ...]
               支持 2 元组和 4 元组两种格式，向后兼容。
        keyword_overlap_min: 关键词交集最小数量（粗筛阈值）
        similarity_threshold: SequenceMatcher 相似度阈值（精筛阈值）

    Returns:
        去重后的伏笔列表，每条：
        {
            "id": "fs_001",           # 顺序编号
            "clue": str,              # 代表条目（首次出现的 clue）
            "first_seen": int,        # 首次出现章节
            "last_seen": int,         # 最后出现章节
            "evidence_chapters": [int], # 所有出现章节
            "merged_count": int,      # 合并了多少条原始线索
            "type": str,              # 伏笔类型（代表条目的 type，可能为空）
            "confidence": str,        # 置信度（代表条目的 confidence，可能为空）
        }
    """
    if not clues:
        return []

    # 预处理：每条 clue 提取关键词 + 归一化文本，兼容 2 元组和 4 元组
    entries = []
    for item in clues:
        if len(item) >= 4:
            chapter, clue_text, ftype, conf = item[0], item[1], item[2], item[3]
        elif len(item) >= 2:
            chapter, clue_text = item[0], item[1]
            ftype, conf = "", ""
        else:
            continue
        if not clue_text or not str(clue_text).strip():
            continue
        entries.append({
            'chapter': chapter,
            'clue': str(clue_text).strip(),
            'keywords': _extract_keywords(str(clue_text)),
            'normalized': _normalize_for_dedup(str(clue_text)),
            'type': str(ftype) if ftype else "",
            'confidence': str(conf) if conf else "",
        })

    if not entries:
        return []

    # 分组：每条 entry 归入某个"伏笔组"
    groups = []  # 每组 = [entry_indices...]

    for i, entry in enumerate(entries):
        matched_group = None

        for g_idx, group in enumerate(groups):
            # 取组内第一条作为代表
            rep = entries[group[0]]

            # 粗筛：关键词交集
            overlap = entry['keywords'] & rep['keywords']
            if len(overlap) < keyword_overlap_min:
                continue

            # 精筛：SequenceMatcher
            ratio = _difflib.SequenceMatcher(
                None, entry['normalized'], rep['normalized']
            ).ratio()
            if ratio >= similarity_threshold:
                matched_group = g_idx
                break

        if matched_group is not None:
            groups[matched_group].append(i)
        else:
            groups.append([i])

    # 构建去重结果
    catalog = []
    for g_idx, group in enumerate(groups):
        members = [entries[i] for i in group]
        # 取首次出现的 clue 作为代表
        members.sort(key=lambda e: e['chapter'])
        rep = members[0]
        all_chapters = sorted(set(e['chapter'] for e in members))

        # 取最高置信度（高>中>低）
        conf_order = {"高": 3, "中": 2, "低": 1}
        best_conf = max(members, key=lambda e: conf_order.get(e['confidence'], 0))
        # 取最常见的 type
        type_counts = {}
        for m in members:
            if m['type']:
                type_counts[m['type']] = type_counts.get(m['type'], 0) + 1
        best_type = max(type_counts, key=type_counts.get) if type_counts else rep['type']

        catalog.append({
            'id': f"fs_{g_idx + 1:03d}",
            'clue': rep['clue'],
            'first_seen': all_chapters[0],
            'last_seen': all_chapters[-1],
            'evidence_chapters': all_chapters,
            'merged_count': len(members),
            'type': best_type,
            'confidence': best_conf['confidence'],
        })

    return catalog
