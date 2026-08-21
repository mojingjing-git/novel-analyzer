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


_CONSOLIDATION_SYSTEM_PROMPT = """你是中文地名归一化审核员。下面是若干地点的 canonical_name 与 aliases 列表。
请识别哪些组指向**同一个真实地点**（不是同类型，是完全相同的一个地方）。

## 判断规则

- 字面别名通常意味着同一地点的不同写法（如 "宁安县" vs "宁安县城"）
- 不要合并同类型但不同地方的（如 "宁安县城" 与 "宁安县城天牛坊" 不是同一地点）
- 不要合并不同朝代/不同世界的同名地点（如两个故事里的"京城"）

## 输出 JSON

{"merges": [["宁安县", "宁安县城"], ["大贞", "大贞王朝"], ...]}
merges 是字面字符串二元组列表，每个二元组是同一地点的两个 canonical 写法。
没有需要合并的：{"merges": []}
"""


def build_consolidation_prompt(
    canonical_list: List[Dict[str, Any]],
    batch_idx: int,
    total_batches: int,
) -> List[Dict[str, str]]:
    user_lines = [
        f"## 候选 canonical 列表（batch {batch_idx}/{total_batches}，共 {len(canonical_list)} 个）",
        "",
    ]
    for i, c in enumerate(canonical_list, 1):
        aliases_str = ", ".join(c["aliases"])
        user_lines.append(f"[{i}] canonical={c['canonical']}, aliases=[{aliases_str}]")
    user_lines.append("")
    user_lines.append("请按 system prompt 规则输出 JSON")
    return [
        {"role": "system", "content": _CONSOLIDATION_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def parse_merge_map(llm_response: str, valid_names: set) -> Dict[str, List]:
    """解析 consolidation LLM 输出，校验每个名字必须在 valid_names 中"""
    parsed = safe_parse_json(llm_response)
    if not parsed or not isinstance(parsed, dict):
        return {"merges": []}
    raw_merges = parsed.get("merges", [])
    if not isinstance(raw_merges, list):
        return {"merges": []}

    valid_merges: List[List[str]] = []
    for pair in raw_merges:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        a, b = pair[0], pair[1]
        if not isinstance(a, str) or not isinstance(b, str):
            continue
        if a == b:
            continue
        if a not in valid_names or b not in valid_names:
            logger.warning(f"丢弃幻觉 merge：{pair}（valid_names={len(valid_names)}）")
            continue
        valid_merges.append([a, b])
    return {"merges": valid_merges}


def apply_merges(
    all_canonicals: List[Dict[str, Any]],
    merge_map: Dict[str, List[List[str]]],
) -> List[Dict[str, Any]]:
    """机械应用 merge_map 到 all_canonicals

    对每个 [a, b] 二元组，把 a 的字段合并到 b（aliases 取并集，type/parent 取并集去重计数，
    description 取最长），最后删除 a。
    chain merge：A→B + B→C 合并为单条目。
    """
    if not merge_map.get("merges"):
        return all_canonicals

    parent_uf: Dict[str, str] = {}
    for c in all_canonicals:
        parent_uf[c["canonical_name"]] = c["canonical_name"]

    def _find(x):
        while parent_uf[x] != x:
            parent_uf[x] = parent_uf[parent_uf[x]]
            x = parent_uf[x]
        return x

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            ra_cnt = next((c.get("chapter_count", 0) for c in all_canonicals if c["canonical_name"] == ra), 0)
            rb_cnt = next((c.get("chapter_count", 0) for c in all_canonicals if c["canonical_name"] == rb), 0)
            if ra_cnt >= rb_cnt:
                parent_uf[rb] = ra
            else:
                parent_uf[ra] = rb

    for pair in merge_map["merges"]:
        if pair[0] in parent_uf and pair[1] in parent_uf:
            _union(pair[0], pair[1])

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in all_canonicals:
        root = _find(c["canonical_name"])
        groups.setdefault(root, []).append(c)

    result: List[Dict[str, Any]] = []
    for root, items in groups.items():
        if len(items) == 1:
            result.append(items[0])
            continue
        all_aliases = sorted(set(a for it in items for a in it["aliases"]))
        merged: Dict[str, Any] = {
            "canonical_name": root,
            "aliases": all_aliases,
            "parent": "",
            "description": max((it["description"] for it in items), key=len, default=""),
            "chapter_count": max(it.get("chapter_count", 0) for it in items),
        }
        result.append(merged)
    result.sort(key=lambda c: -c.get("chapter_count", 0))
    return result


# ---------------------------------------------------------------------------
# Phase 0 orchestration（Task 6）
# ---------------------------------------------------------------------------
import asyncio
import hashlib
from datetime import datetime
from typing import Set

from backend.core.llm_client import LLMClient
from backend.utils.json_utils import safe_load_json, safe_save_json


_LOCATIONS_NORMALIZED_FILENAME = "locations_normalized.json"
_SPATIAL_NORMALIZED_FILENAME = "spatial_relationships_normalized.json"
_LOCATIONS_BATCH_SIZE = 1000
_SPATIAL_BATCH_SIZE = 1000
_CONSOL_BATCH_SIZE = 2000


class LocationNormalizer:
    """地点与空间关系归一化（Phase 0）

    流程：
    - _run_phase_0a_batches: 切片 locations → 并发 LLM → 校验
    - _run_phase_0a_consolidate: 1+ 次 LLM 合并跨 batch 同地点
    - _run_phase_0b_batches: 切片 spatial + canonical 白名单 → 并发 LLM
    - _run_phase_0b_dedupe: 机械去重同 (from, to)
    - 持久化到 output/{_LOCATIONS_NORMALIZED_FILENAME, _SPATIAL_NORMALIZED_FILENAME}
    - 给每章 chapter_*.json 加 _normalized_ref + _normalized_spatial_ref
    """

    def __init__(
        self,
        output_dir: Path,
        llm_client: LLMClient,
        concurrency: int = 1,
        locations_batch_size: int = _LOCATIONS_BATCH_SIZE,
        spatial_batch_size: int = _SPATIAL_BATCH_SIZE,
    ):
        self.output_dir = Path(output_dir)
        self.llm_client = llm_client
        self.concurrency = max(1, concurrency)
        self.locations_batch_size = max(1, locations_batch_size)
        self.spatial_batch_size = max(1, spatial_batch_size)

    async def run(self) -> bool:
        """跑完整 Phase 0；任一阶段失败抛异常或返回 False"""
        # 1. 读取所有 chapter_*.json
        raw_locations, raw_spatial = self._load_all_chapter_data()
        if not raw_locations:
            logger.info("无 locations 数据，跳过 Phase 0")
            return False

        # 2. Phase 0a-batches：并发归一化 locations
        groups = aggregate_locations(raw_locations)
        logger.info(f"Phase 0a：{len(groups)} 个 location group")
        all_canonicals = await self._run_phase_0a_batches(groups)
        if all_canonicals is None:
            return False

        # 3. Phase 0a-consol：合并跨 batch 同地点
        final_locations = await self._run_phase_0a_consolidate(all_canonicals)
        if final_locations is None:
            return False

        # 4. Phase 0b-batches：并发结构化 spatial
        pairs = aggregate_spatial_pairs(raw_spatial)
        logger.info(f"Phase 0b：{len(pairs)} 个 spatial pair")
        whitelist = [{"canonical": c["canonical_name"], "aliases": c["aliases"]} for c in final_locations]
        whitelist_names: Set[str] = {c["canonical_name"] for c in final_locations}
        all_spatial = await self._run_phase_0b_batches(pairs, whitelist, whitelist_names)
        if all_spatial is None:
            return False

        # 5. Phase 0b-dedupe：机械去重
        final_spatial = self._run_phase_0b_dedupe(all_spatial)

        # 6. 持久化
        self._persist_normalized_locations(final_locations)
        self._persist_normalized_spatial(final_spatial)
        self._add_normalized_ref_to_chapters()
        logger.info("Phase 0 完成")
        return True

    # ---- 数据读取 ----

    def _output_subdir(self) -> Path:
        sub = self.output_dir / "output"
        return sub if sub.is_dir() else self.output_dir

    def _load_all_chapter_data(self) -> tuple:
        """从 output_dir/output/chapter_*.json 聚合 locations + spatial_relationships"""
        raw_locations = []
        raw_spatial = []
        output_subdir = self._output_subdir()
        for cf in sorted(output_subdir.glob("chapter_*_result.json")):
            data = safe_load_json(cf)
            if not data:
                continue
            ch = data.get("chapter_number")
            if ch is None:
                continue
            for loc in data.get("locations", []) or []:
                loc_copy = dict(loc)
                loc_copy["chapter"] = ch
                raw_locations.append(loc_copy)
            for rel in data.get("spatial_relationships", []) or []:
                rel_copy = dict(rel)
                rel_copy["chapter"] = ch
                raw_spatial.append(rel_copy)
        return raw_locations, raw_spatial

    # ---- Phase 0a ----

    async def _run_phase_0a_batches(self, groups):
        batches = self._split_into_batches(groups, self.locations_batch_size)
        if not batches:
            return []
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _process(batch_idx, batch):
            async with semaphore:
                messages = build_location_prompt(batch, batch_idx, len(batches))
                try:
                    success, content, error, _tokens = await self.llm_client.chat(messages)
                except Exception as e:
                    logger.error(f"Phase 0a batch {batch_idx} 失败: {e}")
                    return None
                if not success:
                    logger.error(f"Phase 0a batch {batch_idx} 失败: {error}")
                    return None
                return parse_and_validate_locations(content, batch)

        tasks = [_process(i, b) for i, b in enumerate(batches, 1)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        all_canonicals = []
        for r in results:
            if r is None:
                return None
            all_canonicals.extend(r)
        return all_canonicals

    async def _run_phase_0a_consolidate(self, all_canonicals):
        if len(all_canonicals) <= 2:
            return all_canonicals
        canonical_list = [
            {"canonical": c["canonical_name"], "aliases": c["aliases"]}
            for c in all_canonicals
        ]
        valid_names = {c["canonical"] for c in canonical_list}
        all_merges = []
        for i in range(0, len(canonical_list), _CONSOL_BATCH_SIZE):
            batch = canonical_list[i:i + _CONSOL_BATCH_SIZE]
            batch_idx = i // _CONSOL_BATCH_SIZE + 1
            total_batches = (len(canonical_list) + _CONSOL_BATCH_SIZE - 1) // _CONSOL_BATCH_SIZE
            messages = build_consolidation_prompt(batch, batch_idx, total_batches)
            try:
                success, content, error, _tokens = await self.llm_client.chat(messages)
            except Exception as e:
                logger.error(f"Phase 0a consol 失败: {e}")
                return None
            if not success:
                logger.error(f"Phase 0a consol 失败: {error}")
                return None
            partial = parse_merge_map(content, valid_names)
            all_merges.extend(partial["merges"])
        return apply_merges(all_canonicals, {"merges": all_merges})

    # ---- Phase 0b ----

    async def _run_phase_0b_batches(self, pairs, whitelist, whitelist_names):
        if not pairs:
            return []
        batches = self._split_into_batches(pairs, self.spatial_batch_size)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _process(batch_idx, batch):
            async with semaphore:
                messages = build_spatial_prompt(batch, whitelist, batch_idx, len(batches))
                try:
                    success, content, error, _tokens = await self.llm_client.chat(messages)
                except Exception as e:
                    logger.error(f"Phase 0b batch {batch_idx} 失败: {e}")
                    return None
                if not success:
                    logger.error(f"Phase 0b batch {batch_idx} 失败: {error}")
                    return None
                return parse_and_validate_spatial(content, whitelist_names)

        tasks = [_process(i, b) for i, b in enumerate(batches, 1)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        all_spatial = []
        for r in results:
            if r is None:
                return None
            all_spatial.extend(r)
        return all_spatial

    @staticmethod
    def _run_phase_0b_dedupe(all_spatial):
        """同 (from, to) 对的多 batch 结果合并：evidence_chapters 取并集"""
        pair_meta = {}
        for rel in all_spatial:
            key = (rel["from"], rel["to"])
            if key not in pair_meta:
                pair_meta[key] = dict(rel)
            else:
                existing = pair_meta[key]
                existing["evidence_chapters"] = sorted(set(
                    existing["evidence_chapters"] + rel["evidence_chapters"]
                ))
        return list(pair_meta.values())

    # ---- 工具 ----

    @staticmethod
    def _split_into_batches(items, batch_size):
        return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

    # ---- 持久化 ----

    def _persist_normalized_locations(self, locations) -> None:
        output_subdir = self._output_subdir()
        payload = {
            "schema_version": 1,
            "normalized_at": datetime.now().isoformat(timespec="seconds"),
            "model": getattr(self.llm_client, "model", "unknown"),
            "chapter_mtimes_hash": self._compute_chapter_mtimes_hash(),
            "locations": [
                {
                    "canonical_name": loc["canonical_name"],
                    "aliases": loc["aliases"],
                    "parent": loc.get("parent", ""),
                    "type": loc.get("type", ""),
                    "description": loc.get("description", ""),
                    "chapter_count": loc.get("chapter_count", 0),
                }
                for loc in locations
            ],
        }
        out_path = output_subdir / _LOCATIONS_NORMALIZED_FILENAME
        safe_save_json(payload, out_path)

    def _persist_normalized_spatial(self, spatial) -> None:
        output_subdir = self._output_subdir()
        payload = {
            "schema_version": 1,
            "normalized_at": datetime.now().isoformat(timespec="seconds"),
            "model": getattr(self.llm_client, "model", "unknown"),
            "chapter_mtimes_hash": self._compute_chapter_mtimes_hash(),
            "relationships": spatial,
        }
        out_path = output_subdir / _SPATIAL_NORMALIZED_FILENAME
        safe_save_json(payload, out_path)

    def _compute_chapter_mtimes_hash(self) -> str:
        output_subdir = self._output_subdir()
        mtimes = []
        for cf in sorted(output_subdir.glob("chapter_*_result.json")):
            mtimes.append(f"{cf.name}:{int(cf.stat().st_mtime)}")
        return hashlib.md5("\n".join(mtimes).encode("utf-8")).hexdigest()

    def _add_normalized_ref_to_chapters(self) -> None:
        """给每章 chapter_*.json 顶部加 _normalized_ref + _normalized_spatial_ref 字段"""
        output_subdir = self._output_subdir()
        for cf in output_subdir.glob("chapter_*_result.json"):
            data = safe_load_json(cf)
            if not data:
                continue
            data["_normalized_ref"] = _LOCATIONS_NORMALIZED_FILENAME
            data["_normalized_spatial_ref"] = _SPATIAL_NORMALIZED_FILENAME
            safe_save_json(data, cf)