"""
可视化数据聚合服务
从 result.json 聚合时间线/关系图/地图所需数据
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

from backend.config.constants import (
    FORESHADOW_CATEGORY_DEFS,
    FORESHADOW_CATEGORY_FALLBACK,
    FORESHADOW_CATEGORY_SCHEMA_VERSION,
    FORESHADOW_TYPE_MAP_FILE,
)
from backend.models.analysis_result import AnalysisResult
from backend.utils.aggregate_utils import JSONAggregator

logger = logging.getLogger(__name__)


def _load_results(output_dir: Path) -> List[AnalysisResult]:
    aggregator = JSONAggregator(output_dir)
    return aggregator.load_all_chapters()


def _load_type_category_map(output_dir: Path) -> Dict[str, str]:
    """
    加载 per-book type→category 映射（final_summary 服务持久化的文件）。
    映射缺失/schema 不匹配/解析失败：返回空 dict，调用方降级到原始 type 显示。
    """
    map_path = output_dir / FORESHADOW_TYPE_MAP_FILE
    if not map_path.exists():
        return {}
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema_version") != FORESHADOW_CATEGORY_SCHEMA_VERSION:
            return {}
        return data.get("mapping", {}) or {}
    except Exception as e:
        logger.debug(f"viz_service 加载 type 映射失败: {e}，按原始 type 降级显示")
        return {}


def timeline_data(output_dir: Path) -> Dict[str, Any]:
    """时间线数据：核心事件泳道 + 伏笔埋设/回收"""
    results = _load_results(output_dir)
    type_to_cat = _load_type_category_map(output_dir)
    valid_names = {name for name, _, _ in FORESHADOW_CATEGORY_DEFS}
    events = []
    foreshadows = []

    for result in results:
        ch = result.chapter_number
        for event in result.core_events:
            events.append({
                "chapter": ch,
                "id": event.id,
                "event": event.event,
                "characters": event.characters,
                "function": event.function,
                "importance": event.importance,
            })
        for fs in result.foreshadowing:
            ftype = fs.type or ""
            # 源头约束落地后 type 即合法类别；旧数据才查映射兜底
            if ftype in valid_names:
                category = ftype
            else:
                category = type_to_cat.get(ftype, FORESHADOW_CATEGORY_FALLBACK)
            foreshadows.append({
                "chapter": ch,
                "clue": fs.clue,
                "type": ftype,
                "category": category,
                "confidence": fs.confidence,
                "importance": fs.importance,
            })

    return {
        "events": events,
        "foreshadows": foreshadows,
        "chapters": [r.chapter_number for r in results],
        "category_map_loaded": bool(type_to_cat),
    }


def graph_data(output_dir: Path) -> Dict[str, Any]:
    """关系图数据：角色作为节点，同章出现/关系变化作为边"""
    results = _load_results(output_dir)
    nodes_map: Dict[str, Dict[str, Any]] = {}
    edges_map: Dict[str, Dict[str, Any]] = {}

    def normalize_name(name: str) -> str:
        # 去除常见后缀
        for suffix in ('（主角）', '（配角）', '（已故）', '（重要）', '（次要）',
                       '（女主）', '（男主）', '（反派）', '（龙套）',
                       '(主角)', '(配角)', '(已故)', '(重要)', '(次要)',
                       '(女主)', '(男主)', '(反派)', '(龙套)'):
            if name.endswith(suffix):
                return name[:-len(suffix)].strip()
        return name.strip()

    for result in results:
        ch = result.chapter_number
        chars_in_chapter = set()
        for event in result.core_events:
            raw_chars = event.characters
            if isinstance(raw_chars, list):
                raw_chars = ", ".join(str(c) for c in raw_chars)
            chars = [normalize_name(c) for c in str(raw_chars).replace("，", ",").split(",") if c.strip()]
            for c in chars:
                chars_in_chapter.add(c)
                if c not in nodes_map:
                    nodes_map[c] = {"id": c, "name": c, "chapters": [], "event_count": 0}
                nodes_map[c]["chapters"].append(ch)
                nodes_map[c]["event_count"] += 1

        # 同章角色建边
        chars = sorted(chars_in_chapter)
        for i in range(len(chars)):
            for j in range(i + 1, len(chars)):
                a, b = chars[i], chars[j]
                key = f"{a}|{b}"
                if key not in edges_map:
                    edges_map[key] = {"source": a, "target": b, "chapters": [], "weight": 0}
                edges_map[key]["chapters"].append(ch)
                edges_map[key]["weight"] += 1

    return {
        "nodes": [{"id": n["id"], "name": n["name"], "event_count": n["event_count"]} for n in nodes_map.values()],
        "edges": [{"source": e["source"], "target": e["target"], "weight": e["weight"]} for e in edges_map.values()],
    }


def map_data(output_dir: Path) -> Dict[str, Any]:
    """地图数据：地点层级 + 空间关系"""
    results = _load_results(output_dir)
    locations: Dict[str, Dict[str, Any]] = {}
    spatial_rels: List[Dict[str, str]] = []

    for result in results:
        ch = result.chapter_number
        for loc in result.locations:
            name = loc.name.strip()
            if not name:
                continue
            if name not in locations:
                locations[name] = {
                    "id": name,
                    "name": name,
                    "parent": loc.parent,
                    "type": loc.type,
                    "description": loc.description,
                    "chapters": [],
                }
            locations[name]["chapters"].append(ch)
        for rel in result.spatial_relationships:
            if rel.from_ and rel.to:
                spatial_rels.append({"from": rel.from_, "to": rel.to, "relation": rel.relation})

    return {
        "locations": list(locations.values()),
        "relationships": spatial_rels,
    }
