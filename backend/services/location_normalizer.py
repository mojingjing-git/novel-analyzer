"""
地点与空间关系归一化（2026-08-21 spec）

Phase 0 在最终总结之前跑：
- 0a-batches: 并发 LLM 归一化 locations
- 0a-consol: 1 次 LLM 合并跨 batch 同地点
- 0b-batches: 并发 LLM 结构化 spatial_relationships
- 0b-dedupe: 机械去重同 (from, to) 对
"""
import json
import logging
from collections import Counter
from difflib import SequenceMatcher as _SM
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.utils.text_utils import is_same_location
from backend.utils.json_utils import safe_parse_json

logger = logging.getLogger(__name__)


def aggregate_locations(raw_locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """预聚合：字面同名 + 相似名（SequenceMatcher ≥ 0.85）→ group

    每个 group 包含 aliases（字面名列表）、types（Counter）、parents（Counter）、
    chapter_count、sample_desc（任一非空描述）。
    按 chapter_count 降序返回。
    """
    name_meta: Dict[str, Dict[str, Any]] = {}
    for loc in raw_locations:
        n = (loc.get("name") or "").strip()
        if not n:
            continue
        if n not in name_meta:
            name_meta[n] = {
                "types": Counter(),
                "parents": Counter(),
                "descs": set(),
                "chapters": set(),
            }
        m = name_meta[n]
        t = (loc.get("type") or "").strip()
        m["types"][t] += 1
        p = (loc.get("parent") or "").strip()
        if p:
            m["parents"][p] += 1
        d = (loc.get("description") or "").strip()
        if d:
            m["descs"].add(d)
        if "chapter" in loc and loc["chapter"] is not None:
            m["chapters"].add(loc["chapter"])

    # Union-Find
    parent_uf = {n: n for n in name_meta}

    def _find(x):
        while parent_uf[x] != x:
            parent_uf[x] = parent_uf[parent_uf[x]]
            x = parent_uf[x]
        return x

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent_uf[ra] = rb

    names = list(name_meta.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if is_same_location(names[i], names[j]):
                _union(names[i], names[j])

    groups_dict: Dict[str, List[str]] = {}
    for n in names:
        groups_dict.setdefault(_find(n), []).append(n)

    result: List[Dict[str, Any]] = []
    for aliases in groups_dict.values():
        agg_types, agg_parents, agg_descs, agg_chapters = Counter(), Counter(), set(), set()
        for a in aliases:
            m = name_meta[a]
            agg_types.update(m["types"])
            agg_parents.update(m["parents"])
            agg_descs |= m["descs"]
            agg_chapters |= m["chapters"]
        result.append({
            "aliases": sorted(aliases, key=len),
            "types": dict(agg_types),
            "parents": dict(agg_parents),
            "chapter_count": len(agg_chapters),
            "sample_desc": next(iter(agg_descs), ""),
        })

    result.sort(key=lambda g: -g["chapter_count"])
    return result


def aggregate_spatial_pairs(raw_rels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """预聚合 spatial_relationships：相同 (from, to) 对的多条记录合并为 1 条

    返回按 chapters 数量降序的 pair 列表，每个 dict 包含 from/to/relations(chapters)/chapters(list)。
    """
    pair_meta: Dict[tuple, Dict[str, Any]] = {}
    for rel in raw_rels:
        f = (rel.get("from") or "").strip()
        t = (rel.get("to") or "").strip()
        if not f or not t:
            continue
        key = (f, t)
        if key not in pair_meta:
            pair_meta[key] = {"relations": set(), "chapters": set()}
        rel_text = (rel.get("relation") or "").strip()
        pair_meta[key]["relations"].add(rel_text)
        if "chapter" in rel and rel["chapter"] is not None:
            pair_meta[key]["chapters"].add(rel["chapter"])

    pairs: List[Dict[str, Any]] = [
        {
            "from": k[0],
            "to": k[1],
            "relations": sorted(v["relations"]),
            "chapters": sorted(v["chapters"]),
        }
        for k, v in pair_meta.items()
    ]
    pairs.sort(key=lambda p: -len(p["chapters"]))
    return pairs


_LOCATION_SYSTEM_PROMPT = """你是中文网络小说数据归一化专家。你的任务是把同一地点在不同章节里的不同写法/不同分类合并为规范条目。

## 任务 1 — 地点归一化（locations）

输入是一组预聚合的地点，每个 group 包含：
- 该组里出现过的所有原始地名（aliases）
- 这些地点的所有 type 变体 + 各自次数
- 这些地点的所有 parent 变体 + 各自次数
- 出现总章节数
- 1 个样本描述（其他描述类似）

对每个 group，你需要输出：
{
  "canonical_name": "宁安县",        // 字面等于 aliases 中的某一个
  "aliases": ["宁安县", "宁安县城", "宁安县桐树坊"],   // 合并原始所有名称
  "parent": "京畿府",                // 最合理的上级；必须是某个原始 parent，否则 ""
  "type": "县城",                    // 选最合适的类型；可基于已有 type 创建新类
  "description": "大贞王朝下辖县城，..."   // 取最完整的描述
}

## 硬约束（违反则视为输出错误）

1. canonical_name 必须字面等于 aliases 中的某一个字符串。不容许改字、删字、合并字。
2. aliases 必须包含输入时该 group 的所有原始名称（字面保留，不得遗漏或改写）。
3. 输出必须是合法 JSON，locations 数组可为空。
"""


def build_location_prompt(
    groups: List[Dict[str, Any]],
    batch_idx: int,
    total_batches: int,
) -> List[Dict[str, str]]:
    """构造 locations 归一化的 system + user 消息"""
    user_lines = [f"## 待归一化的地点（batch {batch_idx}/{total_batches}，共 {len(groups)} 个 group）", ""]
    for i, g in enumerate(groups, 1):
        aliases_str = ", ".join(g["aliases"])
        types_str = ", ".join(f"{k}:{v}" for k, v in g["types"].items())
        parents_str = ", ".join(f"{k}:{v}" for k, v in g["parents"].items())
        user_lines.append(
            f"[{i}] aliases=[{aliases_str}]\n"
            f"    types={{{types_str}}}\n"
            f"    parents={{{parents_str}}}\n"
            f"    chapter_count={g['chapter_count']}\n"
            f"    sample_desc=\"{g['sample_desc']}\""
        )
    user_lines.append("")
    user_lines.append("请按 system prompt 规则输出 JSON {\"locations\": [...]}")
    return [
        {"role": "system", "content": _LOCATION_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def parse_and_validate_locations(
    llm_response: str,
    batch: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """解析 LLM 输出并校验：canonical_name 必须字面等于某个 alias

    返回清洗后的 canonical 列表。无效条目被丢弃。
    """
    parsed = safe_parse_json(llm_response)
    if not parsed or not isinstance(parsed, dict):
        return []
    raw_locations = parsed.get("locations", [])
    if not isinstance(raw_locations, list):
        return []

    valid: List[Dict[str, Any]] = []
    for loc in raw_locations:
        if not isinstance(loc, dict):
            continue
        canonical = loc.get("canonical_name", "")
        aliases = loc.get("aliases", [])
        if not isinstance(canonical, str) or not isinstance(aliases, list):
            continue
        aliases = [a for a in aliases if isinstance(a, str)]
        # 硬约束1：canonical 必须字面等于某个 alias
        if canonical not in aliases:
            logger.warning(f"丢弃幻觉 location：canonical='{canonical}' 不在 aliases={aliases}")
            continue
        # 必填字段兜底
        valid.append({
            "canonical_name": canonical,
            "aliases": sorted(set(aliases)),
            "parent": loc.get("parent", "") or "",
            "type": loc.get("type", "") or "",
            "description": loc.get("description", "") or "",
        })
    return valid


_SPATIAL_SYSTEM_PROMPT = """你是中文小说空间关系结构化专家。你的任务是把每条自由文本空间关系描述抽取为结构化字段。

## 任务 — 空间关系结构化（spatial_relationships）

输入是一组预聚合的 (from, to) 关系对，每对包含：
- 原始 from / to 地名（已与地点归一化的白名单一致）
- 多条自由文本 relation 描述
- 出现的章节列表

对每个 pair，你需要输出：
{
  "from": "宁安县",                  // 必须引用白名单中的 canonical_name
  "to": "德胜府",                    // 同上
  "direction": "东南",              // 原文中的方向；无明确则 ""
  "distance_text": "约两三百里",    // 原文距离描述；无明确则 ""
  "distance_estimate_km": 130,      // 估算公里数（整数）；无明确数字则 null
  "relation_type": "相邻",          // 包含/相邻/接壤/隔海/跨越/途经/...；无法判断则 ""
  "evidence_chapters": [42, 43]     // 必须引用原始章节号数组（非空）
}

## 硬约束（违反则视为输出错误）

1. from / to 必须字面等于用户输入"白名单"中的某个 canonical_name，不容许创造新地名。
2. evidence_chapters 必须是非空整数数组。
3. 输出必须是合法 JSON，relationships 数组可为空。
"""


def build_spatial_prompt(
    pairs: List[Dict[str, Any]],
    whitelist: List[Dict[str, Any]],
    batch_idx: int,
    total_batches: int,
) -> List[Dict[str, str]]:
    """构造 spatial 结构化的 system + user 消息"""
    user_lines = [
        f"## 可用地名白名单（{len(whitelist)} 个）",
        "",
    ]
    for i, w in enumerate(whitelist, 1):
        aliases_str = ", ".join(w["aliases"])
        user_lines.append(f"[L{i}] canonical={w['canonical']}, aliases=[{aliases_str}]")
    user_lines.append("")
    user_lines.append(
        f"## 待结构化的空间关系（batch {batch_idx}/{total_batches}，共 {len(pairs)} 个 pair）"
    )
    user_lines.append("")
    for i, p in enumerate(pairs, 1):
        relations_str = "; ".join(f"\"{r}\"" for r in p["relations"])
        chapters_str = ", ".join(str(c) for c in p["chapters"])
        user_lines.append(
            f"[P{i}] from={p['from']}, to={p['to']}\n"
            f"    relations=[{relations_str}]\n"
            f"    chapters=[{chapters_str}]"
        )
    user_lines.append("")
    user_lines.append("请按 system prompt 规则输出 JSON {\"relationships\": [...]}")
    return [
        {"role": "system", "content": _SPATIAL_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def parse_and_validate_spatial(
    llm_response: str,
    whitelist_names: set,
) -> List[Dict[str, Any]]:
    """解析 LLM 输出并校验：from/to 必须字面在白名单中"""
    parsed = safe_parse_json(llm_response)
    if not parsed or not isinstance(parsed, dict):
        return []
    raw_rels = parsed.get("relationships", [])
    if not isinstance(raw_rels, list):
        return []

    valid: List[Dict[str, Any]] = []
    for rel in raw_rels:
        if not isinstance(rel, dict):
            continue
        f, t = rel.get("from", ""), rel.get("to", "")
        if f not in whitelist_names or t not in whitelist_names:
            logger.warning(f"丢弃幻觉 spatial：from='{f}' to='{t}'（不在白名单）")
            continue
        chapters = rel.get("evidence_chapters", [])
        if not isinstance(chapters, list) or not chapters:
            continue
        chapters_clean = [int(c) for c in chapters if isinstance(c, (int, float))]
        if not chapters_clean:
            continue
        valid.append({
            "from": f,
            "to": t,
            "direction": rel.get("direction", "") or "",
            "distance_text": rel.get("distance_text", "") or "",
            "distance_estimate_km": rel.get("distance_estimate_km"),
            "relation_type": rel.get("relation_type", "") or "",
            "evidence_chapters": sorted(set(chapters_clean)),
        })
    return valid