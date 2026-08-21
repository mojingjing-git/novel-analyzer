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