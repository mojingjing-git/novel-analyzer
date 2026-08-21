"""
文本处理工具
提供编码检测、文本截断等功能
（自旧项目 utils/text_utils.py 原样移植）
"""

from pathlib import Path
from typing import Dict, List


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

    # I-7 修复：BOM 与无 BOM 的 UTF-16/32 预判。
    # 原实现 utf-16 排在 gbk 之后，无 BOM 的 UTF-16LE 文件会被 GBK"成功"解码成
    # \x00 乱码（\x00 是合法 GBK 单字节），整书静默损坏且零报错。
    try:
        with open(file_path, 'rb') as f:
            head = f.read(4096)
    except Exception:
        head = b""

    if head.startswith(b'\xff\xfe\x00\x00') or head.startswith(b'\x00\x00\xfe\xff'):
        return 'utf-32'
    if head.startswith(b'\xff\xfe'):
        return 'utf-16'
    if head.startswith(b'\xfe\xff'):
        return 'utf-16-be'
    if head.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'

    if head and head.count(b'\x00') / len(head) > 0.05:
        # 无 BOM 但 NUL 密度高：几乎必然是 UTF-16（BE/LE 各试一次整文件解码）
        for enc in ('utf-16', 'utf-16-be'):
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    f.read()
                return enc
            except UnicodeDecodeError as e:
                last_error = e

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
        clues: [(chapter, clue_text) 或 (chapter, clue_text, type, confidence) 或
                (chapter, clue_text, type, confidence, importance), ...]
               支持 2/4/5 元组多种格式，向后兼容。importance 字段用于透传到结果。
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
            "importance": str,        # 重要度（合并条目中的最高重要度，5 元组时存在）
        }
    """
    if not clues:
        return []

    # 预处理：每条 clue 提取关键词 + 归一化文本，兼容 2/4/5 元组
    entries = []
    for item in clues:
        if len(item) >= 5:
            chapter, clue_text, ftype, conf, imp = (
                item[0], item[1], item[2], item[3], item[4]
            )
        elif len(item) >= 4:
            chapter, clue_text, ftype, conf = item[0], item[1], item[2], item[3]
            imp = ""
        elif len(item) >= 2:
            chapter, clue_text = item[0], item[1]
            ftype, conf, imp = "", "", ""
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
            'importance': str(imp) if imp else "",
        })

    if not entries:
        return []

    # 分组：每条 entry 归入某个"伏笔组"。
    # PERF-2：关键词倒排索引替代"与全部已有组逐一比较"（原实现 O(N²)，
    # 长书数千条线索时需做百万级关键词交集 + SequenceMatcher 精筛）；
    # 现在只与共享 >=1 个关键词的组比较，语义与旧逻辑等价（粗筛阈值不变）。
    groups = []  # 每组 = [entry_indices...]
    keyword_index: Dict[str, set] = {}  # 关键词 -> 含该关键词的组号集合

    for i, entry in enumerate(entries):
        matched_group = None

        # 候选组：与本条目共享任意关键词的组
        candidates = set()
        for kw in entry['keywords']:
            candidates |= keyword_index.get(kw, set())

        for g_idx in sorted(candidates):
            # 取组内第一条作为代表
            rep = entries[groups[g_idx][0]]

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
            g = matched_group
        else:
            groups.append([i])
            g = len(groups) - 1

        # 更新倒排索引
        for kw in entry['keywords']:
            keyword_index.setdefault(kw, set()).add(g)

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
        # 取最高重要度（高>中>低；5 元组输入时存在）
        imp_order = {"高": 3, "中": 2, "低": 1}
        best_imp = max(members, key=lambda e: imp_order.get(e['importance'], 0))
        # 取最常见的 type
        type_counts: Dict[str, int] = {}
        for m in members:
            if m['type']:
                type_counts[m['type']] = type_counts.get(m['type'], 0) + 1
        best_type = max(type_counts, key=lambda k: type_counts[k], default=rep['type'])

        catalog.append({
            'id': f"fs_{g_idx + 1:03d}",
            'clue': rep['clue'],
            'first_seen': all_chapters[0],
            'last_seen': all_chapters[-1],
            'evidence_chapters': all_chapters,
            'merged_count': len(members),
            'type': best_type,
            'confidence': best_conf['confidence'],
            'importance': best_imp['importance'],
        })

    return catalog


# 2026-08-21 修复：对称剥离前后缀修饰符（替换 substring + 后缀白名单）
# 之前 substring 规则只识别后缀（"宁安县城" 中 extra="城" 从尾部切），
# 无法识别前缀修饰（"新大唐公司" 中 shorter="大唐公司" 在中间，extra="司" 也从尾部切）。
# 改为：两端都剥掉已知修饰符后比核心。前缀/后缀对称处理。
import re as _re

_PREFIX_MODS = (
    "新", "老", "旧", "原",         # 短前缀
    "原址", "旧址",                    # 复合前缀
)
_SUFFIX_MODS = (
    "主角", "身边", "附近", "一带", "境内", "内部",
    "已废弃",
    "新址", "原址", "旧址",   # 2026-08-21：与前缀对称，否则"宁安县原址/旧址/新址"漏判
)


def _strip_location_mods(name: str) -> str:
    """从 name 两端循环剥离软修饰符（角色后缀/状态/方位等）

    循环剥离直到稳定，因为前缀可能连续出现（如"旧原址X" → "原址X" → "X"）。
    每次 strip 后 len(s) 严格减小，最多 O(|name|) 次，防死循环。
    """
    s = name
    while True:
        stripped = False
        # 前缀（一旦匹配即跳出本轮循环）
        for p in _PREFIX_MODS:
            if s.startswith(p) and len(s) > len(p) + 1:
                s = s[len(p):]
                stripped = True
                break
        if stripped:
            continue
        # 后缀
        for x in _SUFFIX_MODS:
            if s.endswith(x) and len(s) > len(x) + 1:
                s = s[:-len(x)]
                stripped = True
                break
        if not stripped:
            break
    return s


def is_same_location(n1: str, n2: str) -> bool:
    """判断两个地名是否指向同一地点。

    规则：
    1. 归一化后任一为空或长度 < 2 → False（避免单字噪声）
    2. 归一化后完全相等 → True
    3. 两端剥离已知前后缀修饰符后核心相同 → True（"新大唐公司" vs "大唐公司" → True）

    注：归一化阶段（_normalize_for_dedup）已剥离停用词和标点（含括号）。
    此处只处理语义修饰词。
    """
    a = _normalize_for_dedup(n1)
    b = _normalize_for_dedup(n2)
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    return _strip_location_mods(a) == _strip_location_mods(b)
