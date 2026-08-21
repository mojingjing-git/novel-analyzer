# 地点与空间关系归一化（Phase 0）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `FinalSummaryRunner.run()` 最前面插入 Phase 0，通过 4 子阶段（0a-batches → 0a-consol → 0b-batches → 0b-dedupe）的 LLM 调用，把 `chapter_*.json` 里脏掉的 `locations` 和 `spatial_relationships` 归一化为结构化字段，输出到 `output/locations_normalized.json` 和 `output/spatial_relationships_normalized.json`，并给每章 `chapter_*.json` 加 `_normalized_ref` 引用标记。

**Architecture:** 新建 `LocationNormalizer` 服务类承担归一化全流程；`FinalSummaryRunner.__init__` 内构造并委托；`viz_service.map_data()` 检测 `_normalized_ref` 决定走归一化数据 vs 老逻辑。LLM 设置复用 `APIConfig.summary_model/summary_concurrency/summary_timeout/summary_thinking_mode` 字段（无 UI 改动）。

**Tech Stack:** Python 3.11+ / FastAPI / asyncio / openai SDK / `text_utils.SequenceMatcher` / `json_utils.safe_parse_json` / `dataclasses.replace`

---

## Global Constraints

- Python 3.11+（项目要求）
- FastAPI 异步风格，LLM 调用走 `await self._llm.chat(...)`
- 不修改 `MapPage.vue`、`aggregate_utils.JSONAggregator`（仅改 `viz_service.map_data()` 的读路径）
- 不引入新第三方库（SequenceMatcher 已在 `text_utils.py`）
- 复用 `summary_model` / `summary_concurrency` / `summary_timeout` / `summary_thinking_mode` 配置字段，不新增配置项
- **不动 raw chapter_*.json 原有字段**，仅追加 `_normalized_ref` 和 `_normalized_spatial_ref` 顶部字段
- 不批量转换 CRLF/LF 行尾符
- 任何修改后必须更新 `agent.md` §5/§10

---

## File Structure

| 文件 | 类型 | 职责 |
|---|---|---|
| `backend/utils/text_utils.py` | 改 | 新增 `is_same_location()`（normalize + SequenceMatcher ≥ 0.85）|
| `backend/services/location_normalizer.py` | **新建** | `LocationNormalizer` 类：aggregate / prompt / validate / orchestration / persist |
| `backend/services/final_summary.py` | 改 | `__init__` 构造 `LocationNormalizer`；`run()` 在 `_init_progress` 后插入 `_normalize_phase_0()` |
| `backend/services/viz_service.py` | 改 | `map_data()` 检测 `_normalized_ref` 走归一化数据 |
| `backend/tests/test_location_normalizer.py` | **新建** | 单元测试 + mock LLM 集成测试 |
| `agent.md` | 改 | §5 加新模块说明；§10 加变更记录 |

每个新文件独立可测；测试独立可跑（不依赖其他任务的顺序，仅依赖 Python import）。

---

## Task 1: text_utils.is_same_location

**Files:**
- Modify: `backend/utils/text_utils.py`（在文件末尾追加 `is_same_location` 函数）
- Test: `backend/tests/test_location_normalizer.py`（新建文件）

**Interfaces:**
- Consumes: 两个地名字符串 `n1`, `n2`
- Produces: `bool`（是否指向同一地点）

- [ ] **Step 1: 写测试（test_location_normalizer.py）**

```python
"""地点归一化相关测试（2026-08-21 spec）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.utils.text_utils import is_same_location


class TestIsSameLocation:
    def test_exact_match_returns_true(self):
        assert is_same_location("宁安县", "宁安县") is True

    def test_normalized_match_returns_true(self):
        # 标点不影响
        assert is_same_location("宁安县", "宁安县。") is True

    def test_short_county_vs_full_county_below_threshold(self):
        # "宁安县" vs "宁安县城" — SequenceMatcher ratio = 0.75（4/6）
        # 应判定为不同地点，避免短名误伤
        assert is_same_location("宁安县", "宁安县城") is False

    def test_same_name_with_role_suffix_merges(self):
        # "居安小阁" vs "居安小阁（主角）" — ratio 0.91，合并
        assert is_same_location("居安小阁", "居安小阁（主角）") is True

    def test_completely_different_names_returns_false(self):
        assert is_same_location("大贞", "大秀") is False

    def test_short_strings_below_min_length(self):
        # 长度 ≤ 1 的字符串走不到 SequenceMatcher 分支，直接返回 False
        assert is_same_location("京", "京") is False  # 长度 1，但 normalize 后也是 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_location_normalizer.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_same_location' from 'backend.utils.text_utils'`

- [ ] **Step 3: 实现 `is_same_location`（在 text_utils.py 末尾追加）**

```python
def is_same_location(n1: str, n2: str) -> bool:
    """判断两个地名是否可能指向同一地点（保守规则：normalize 归一 + SequenceMatcher ≥ 0.85）

    不加 substring containment，避免短名误伤（如 "宁安县" 误并入 "宁安县城天牛坊"）。
    """
    from difflib import SequenceMatcher as _SM
    if n1 == n2:
        return True
    a = _normalize_for_dedup(n1)
    b = _normalize_for_dedup(n2)
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    ratio = _SM(None, a, b).ratio()
    return ratio >= 0.85
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestIsSameLocation -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/utils/text_utils.py backend/tests/test_location_normalizer.py
git commit -m "feat(text_utils): add is_same_location for location name fuzzy match"
```

---

## Task 2: aggregate_locations + aggregate_spatial_pairs

**Files:**
- Create: `backend/services/location_normalizer.py`（新建文件，先放纯函数）
- Modify: `backend/tests/test_location_normalizer.py`（追加测试类）

**Interfaces:**
- `aggregate_locations(raw_locations: list[dict]) -> list[dict]` — 每个 dict: `{aliases, types, parents, chapter_count, sample_desc}`
- `aggregate_spatial_pairs(raw_rels: list[dict]) -> list[dict]` — 每个 dict: `{from, to, relations, chapters}`

- [ ] **Step 1: 追加测试（在 test_location_normalizer.py）**

```python
from backend.services.location_normalizer import (
    aggregate_locations,
    aggregate_spatial_pairs,
)


class TestAggregateLocations:
    def test_groups_by_exact_name(self):
        raw = [
            {"name": "宁安县", "parent": "大周王朝", "type": "城市", "description": "县城1", "chapter": 1},
            {"name": "宁安县", "parent": "大周王朝", "type": "城市", "description": "县城2", "chapter": 2},
            {"name": "居安小阁", "parent": "宁安县", "type": "建筑", "description": "小屋", "chapter": 3},
        ]
        groups = aggregate_locations(raw)
        assert len(groups) == 2
        by_name = {g["aliases"][0]: g for g in groups}
        assert by_name["宁安县"]["chapter_count"] == 2
        assert by_name["居安小阁"]["chapter_count"] == 1

    def test_groups_similar_names_via_sequence_matcher(self):
        raw = [
            {"name": "居安小阁", "parent": "宁安县", "type": "建筑", "description": "甲", "chapter": 1},
            {"name": "居安小阁（主角）", "parent": "宁安县", "type": "住宅", "description": "乙", "chapter": 2},
        ]
        groups = aggregate_locations(raw)
        assert len(groups) == 1
        assert set(groups[0]["aliases"]) == {"居安小阁", "居安小阁（主角）"}

    def test_aggregates_types_and_parents(self):
        raw = [
            {"name": "宁安县", "parent": "大周王朝", "type": "城市", "description": "", "chapter": 1},
            {"name": "宁安县", "parent": "大贞", "type": "城池", "description": "", "chapter": 2},
        ]
        groups = aggregate_locations(raw)
        assert groups[0]["types"] == {"城市": 1, "城池": 1}
        assert groups[0]["parents"] == {"大周王朝": 1, "大贞": 1}

    def test_skips_empty_names(self):
        raw = [
            {"name": "", "parent": "", "type": "", "description": "", "chapter": 1},
            {"name": "居安小阁", "parent": "", "type": "建筑", "description": "", "chapter": 2},
        ]
        groups = aggregate_locations(raw)
        assert len(groups) == 1

    def test_sorted_by_chapter_count_desc(self):
        raw = [
            {"name": "低频", "parent": "", "type": "城市", "description": "", "chapter": 1},
            {"name": "高频", "parent": "", "type": "城市", "description": "", "chapter": 1},
            {"name": "高频", "parent": "", "type": "城市", "description": "", "chapter": 2},
            {"name": "高频", "parent": "", "type": "城市", "description": "", "chapter": 3},
        ]
        groups = aggregate_locations(raw)
        assert groups[0]["aliases"] == ["高频"]
        assert groups[1]["aliases"] == ["低频"]


class TestAggregateSpatialPairs:
    def test_groups_same_pair_across_chapters(self):
        raw = [
            {"from": "宁安县", "to": "德胜府", "relation": "位于东南方向，约两三百里", "chapter": 1},
            {"from": "宁安县", "to": "德胜府", "relation": "计缘带陆乘风魂魄飞行约半个时辰可达", "chapter": 2},
        ]
        pairs = aggregate_spatial_pairs(raw)
        assert len(pairs) == 1
        assert pairs[0]["from"] == "宁安县"
        assert pairs[0]["to"] == "德胜府"
        assert len(pairs[0]["relations"]) == 2
        assert pairs[0]["chapters"] == [1, 2]

    def test_separates_pairs_with_different_endpoints(self):
        raw = [
            {"from": "宁安县", "to": "德胜府", "relation": "相邻", "chapter": 1},
            {"from": "宁安县", "to": "大贞", "relation": "从属", "chapter": 1},
        ]
        pairs = aggregate_spatial_pairs(raw)
        assert len(pairs) == 2

    def test_skips_empty_endpoints(self):
        raw = [
            {"from": "", "to": "德胜府", "relation": "相邻", "chapter": 1},
            {"from": "宁安县", "to": "", "relation": "相邻", "chapter": 1},
            {"from": "宁安县", "to": "大贞", "relation": "从属", "chapter": 1},
        ]
        pairs = aggregate_spatial_pairs(raw)
        assert len(pairs) == 1
        assert pairs[0]["to"] == "大贞"

    def test_sorted_by_chapter_count_desc(self):
        raw = [
            {"from": "A", "to": "B", "relation": "", "chapter": 1},
            {"from": "C", "to": "D", "relation": "", "chapter": 1},
            {"from": "C", "to": "D", "relation": "", "chapter": 2},
            {"from": "C", "to": "D", "relation": "", "chapter": 3},
        ]
        pairs = aggregate_spatial_pairs(raw)
        assert pairs[0]["from"] == "C"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestAggregateLocations backend/tests/test_location_normalizer.py::TestAggregateSpatialPairs -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.location_normalizer'`

- [ ] **Step 3: 实现 location_normalizer.py（聚合函数部分）**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestAggregateLocations backend/tests/test_location_normalizer.py::TestAggregateSpatialPairs -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/location_normalizer.py backend/tests/test_location_normalizer.py
git commit -m "feat(location_normalizer): aggregate_locations + aggregate_spatial_pairs"
```

---

## Task 3: build_location_prompt + parse_and_validate_locations

**Files:**
- Modify: `backend/services/location_normalizer.py`（追加 prompt + validate 函数）
- Modify: `backend/tests/test_location_normalizer.py`（追加测试类）

**Interfaces:**
- `build_location_prompt(groups: list[dict], batch_idx: int, total_batches: int) -> list[dict]` — 返回 `[{"role": "system", ...}, {"role": "user", ...}]`
- `parse_and_validate_locations(llm_response: str, batch: list[dict]) -> list[dict]` — 返回清洗后的 canonical 列表

- [ ] **Step 1: 追加测试**

```python
from backend.services.location_normalizer import (
    build_location_prompt,
    parse_and_validate_locations,
)


class TestBuildLocationPrompt:
    def test_returns_system_and_user_messages(self):
        batch = [
            {
                "aliases": ["宁安县", "宁安县城"],
                "types": {"城市": 2},
                "parents": {"大周王朝": 5},
                "chapter_count": 87,
                "sample_desc": "县城描述",
            }
        ]
        messages = build_location_prompt(batch, batch_idx=1, total_batches=5)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "batch 1/5" in messages[1]["content"]
        assert "宁安县" in messages[1]["content"]
        assert "城市:2" in messages[1]["content"]

    def test_system_prompt_includes_hard_constraints(self):
        messages = build_location_prompt([], 1, 1)
        sys = messages[0]["content"]
        assert "canonical_name" in sys
        assert "字面" in sys or "完全匹配" in sys
        assert "JSON" in sys


class TestParseAndValidateLocations:
    def test_accepts_valid_canonical(self):
        batch = [
            {"aliases": ["宁安县", "宁安县城"], "types": {"城市": 2}, "parents": {"大周王朝": 5}, "chapter_count": 87, "sample_desc": ""}
        ]
        llm_text = json.dumps({
            "locations": [
                {"canonical_name": "宁安县", "aliases": ["宁安县", "宁安县城"], "parent": "大周王朝", "type": "县城", "description": "测试"}
            ]
        }, ensure_ascii=False)
        result = parse_and_validate_locations(llm_text, batch)
        assert len(result) == 1
        assert result[0]["canonical_name"] == "宁安县"
        assert "宁安县城" in result[0]["aliases"]

    def test_rejects_hallucinated_canonical(self):
        # LLM 编造了 "京城市" 这个不存在的 alias
        batch = [
            {"aliases": ["宁安县"], "types": {"城市": 1}, "parents": {"大周王朝": 1}, "chapter_count": 5, "sample_desc": ""}
        ]
        llm_text = json.dumps({
            "locations": [
                {"canonical_name": "京城市", "aliases": ["宁安县"], "parent": "", "type": "城市", "description": ""}
            ]
        }, ensure_ascii=False)
        result = parse_and_validate_locations(llm_text, batch)
        assert result == []  # 整条拒收

    def test_handles_empty_locations(self):
        batch = []
        llm_text = json.dumps({"locations": []})
        result = parse_and_validate_locations(llm_text, batch)
        assert result == []

    def test_invalid_json_returns_empty(self):
        result = parse_and_validate_locations("not json {{{", [])
        assert result == []

    def test_keeps_valid_entries_rejects_invalid_in_batch(self):
        batch = [
            {"aliases": ["A"], "types": {"城": 1}, "parents": {"X": 1}, "chapter_count": 1, "sample_desc": ""},
            {"aliases": ["B"], "types": {"城": 1}, "parents": {"X": 1}, "chapter_count": 1, "sample_desc": ""},
        ]
        llm_text = json.dumps({
            "locations": [
                {"canonical_name": "A", "aliases": ["A"], "parent": "X", "type": "城", "description": ""},
                {"canonical_name": "WRONG", "aliases": ["B"], "parent": "X", "type": "城", "description": ""},
            ]
        }, ensure_ascii=False)
        result = parse_and_validate_locations(llm_text, batch)
        assert len(result) == 1
        assert result[0]["canonical_name"] == "A"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestBuildLocationPrompt backend/tests/test_location_normalizer.py::TestParseAndValidateLocations -v`
Expected: FAIL — `ImportError: cannot import name 'build_location_prompt'`

- [ ] **Step 3: 实现 prompt + validate 函数**

```python
import json
from typing import Any, Dict, List

from backend.utils.json_utils import safe_parse_json


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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestBuildLocationPrompt backend/tests/test_location_normalizer.py::TestParseAndValidateLocations -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/location_normalizer.py backend/tests/test_location_normalizer.py
git commit -m "feat(location_normalizer): build_location_prompt + parse_and_validate"
```

---

## Task 4: build_spatial_prompt + parse_and_validate_spatial

**Files:**
- Modify: `backend/services/location_normalizer.py`
- Modify: `backend/tests/test_location_normalizer.py`

**Interfaces:**
- `build_spatial_prompt(pairs: list[dict], whitelist: list[dict], batch_idx: int, total_batches: int) -> list[dict]`
- `parse_and_validate_spatial(llm_response: str, whitelist_names: set[str]) -> list[dict]`

- [ ] **Step 1: 追加测试**

```python
from backend.services.location_normalizer import (
    build_spatial_prompt,
    parse_and_validate_spatial,
)


class TestBuildSpatialPrompt:
    def test_returns_system_and_user_messages(self):
        pairs = [{"from": "宁安县", "to": "德胜府", "relations": ["相邻"], "chapters": [1, 2]}]
        whitelist = [{"canonical": "宁安县", "aliases": ["宁安县"]}, {"canonical": "德胜府", "aliases": ["德胜府"]}]
        messages = build_spatial_prompt(pairs, whitelist, batch_idx=1, total_batches=3)
        assert len(messages) == 2
        assert "batch 1/3" in messages[1]["content"]
        assert "白名单" in messages[1]["content"] or "canonical" in messages[1]["content"]
        assert "宁安县" in messages[1]["content"]

    def test_system_prompt_mentions_whitelist_constraint(self):
        messages = build_spatial_prompt([], [], 1, 1)
        sys = messages[0]["content"]
        assert "canonical_name" in sys or "字面" in sys or "引用" in sys


class TestParseAndValidateSpatial:
    def test_accepts_valid_pair(self):
        llm_text = json.dumps({
            "relationships": [
                {"from": "宁安县", "to": "德胜府", "direction": "东南", "distance_text": "约两三百里", "distance_estimate_km": 130, "relation_type": "相邻", "evidence_chapters": [42]}
            ]
        }, ensure_ascii=False)
        result = parse_and_validate_spatial(llm_text, {"宁安县", "德胜府"})
        assert len(result) == 1
        assert result[0]["from"] == "宁安县"

    def test_rejects_from_not_in_whitelist(self):
        llm_text = json.dumps({
            "relationships": [
                {"from": "京城市", "to": "德胜府", "direction": "", "distance_text": "", "distance_estimate_km": None, "relation_type": "", "evidence_chapters": [1]}
            ]
        }, ensure_ascii=False)
        result = parse_and_validate_spatial(llm_text, {"宁安县", "德胜府"})
        assert result == []

    def test_rejects_to_not_in_whitelist(self):
        llm_text = json.dumps({
            "relationships": [
                {"from": "宁安县", "to": "未知地", "direction": "", "distance_text": "", "distance_estimate_km": None, "relation_type": "", "evidence_chapters": [1]}
            ]
        }, ensure_ascii=False)
        result = parse_and_validate_spatial(llm_text, {"宁安县", "德胜府"})
        assert result == []

    def test_rejects_empty_evidence_chapters(self):
        llm_text = json.dumps({
            "relationships": [
                {"from": "宁安县", "to": "德胜府", "direction": "", "distance_text": "", "distance_estimate_km": None, "relation_type": "", "evidence_chapters": []}
            ]
        }, ensure_ascii=False)
        result = parse_and_validate_spatial(llm_text, {"宁安县", "德胜府"})
        assert result == []

    def test_invalid_json_returns_empty(self):
        result = parse_and_validate_spatial("not json", {"宁安县"})
        assert result == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestBuildSpatialPrompt backend/tests/test_location_normalizer.py::TestParseAndValidateSpatial -v`
Expected: FAIL — `ImportError: cannot import name 'build_spatial_prompt'`

- [ ] **Step 3: 实现**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestBuildSpatialPrompt backend/tests/test_location_normalizer.py::TestParseAndValidateSpatial -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/location_normalizer.py backend/tests/test_location_normalizer.py
git commit -m "feat(location_normalizer): build_spatial_prompt + parse_and_validate"
```

---

## Task 5: build_consolidation_prompt + parse_merge_map + apply_merges

**Files:**
- Modify: `backend/services/location_normalizer.py`
- Modify: `backend/tests/test_location_normalizer.py`

**Interfaces:**
- `build_consolidation_prompt(canonical_list: list[dict], batch_idx: int, total_batches: int) -> list[dict]`
- `parse_merge_map(llm_response: str, valid_names: set[str]) -> dict` — 返回 `{"merges": [[a, b], ...]}`，过滤幻觉
- `apply_merges(all_canonicals: list[dict], merge_map: dict) -> list[dict]` — 机械合并

- [ ] **Step 1: 追加测试**

```python
from backend.services.location_normalizer import (
    build_consolidation_prompt,
    parse_merge_map,
    apply_merges,
)


class TestBuildConsolidationPrompt:
    def test_returns_messages_with_batch_index(self):
        canonical_list = [{"canonical": "宁安县", "aliases": ["宁安县"]}]
        messages = build_consolidation_prompt(canonical_list, batch_idx=2, total_batches=4)
        assert len(messages) == 2
        assert "batch 2/4" in messages[1]["content"]


class TestParseMergeMap:
    def test_accepts_valid_merges(self):
        llm_text = json.dumps({"merges": [["宁安县", "宁安县城"]]})
        result = parse_merge_map(llm_text, {"宁安县", "宁安县城", "大贞"})
        assert result == {"merges": [["宁安县", "宁安县城"]]}

    def test_filters_hallucinated_merges(self):
        # 包含不在 valid_names 的字符串
        llm_text = json.dumps({"merges": [["宁安县", "京城市"]]})
        result = parse_merge_map(llm_text, {"宁安县"})
        assert result == {"merges": []}

    def test_filters_self_merges(self):
        llm_text = json.dumps({"merges": [["宁安县", "宁安县"]]})
        result = parse_merge_map(llm_text, {"宁安县"})
        assert result == {"merges": []}

    def test_empty_merges_is_valid(self):
        llm_text = json.dumps({"merges": []})
        result = parse_merge_map(llm_text, {"宁安县"})
        assert result == {"merges": []}

    def test_invalid_json(self):
        result = parse_merge_map("not json", {"宁安县"})
        assert result == {"merges": []}


class TestApplyMerges:
    def test_merges_two_canonicals(self):
        canon = [
            {"canonical_name": "宁安县", "aliases": ["宁安县"], "parent": "大周", "type": "城市", "description": "甲"},
            {"canonical_name": "宁安县城", "aliases": ["宁安县城"], "parent": "大周", "type": "城池", "description": "乙"},
        ]
        result = apply_merges(canon, {"merges": [["宁安县", "宁安县城"]]})
        assert len(result) == 1
        assert result[0]["canonical_name"] in ("宁安县", "宁安县城")
        assert set(result[0]["aliases"]) == {"宁安县", "宁安县城"}

    def test_no_merges_returns_unchanged(self):
        canon = [{"canonical_name": "宁安县", "aliases": ["宁安县"], "parent": "大周", "type": "城市", "description": ""}]
        result = apply_merges(canon, {"merges": []})
        assert result == canon

    def test_merges_multiple_targets(self):
        canon = [
            {"canonical_name": "A", "aliases": ["A"], "parent": "X", "type": "城", "description": ""},
            {"canonical_name": "B", "aliases": ["B"], "parent": "X", "type": "城", "description": ""},
            {"canonical_name": "C", "aliases": ["C"], "parent": "X", "type": "城", "description": ""},
        ]
        result = apply_merges(canon, {"merges": [["A", "B"], ["B", "C"]]})
        # A 和 B 都合并到 C（A → B → C 链式合并）
        assert len(result) == 1
        assert set(result[0]["aliases"]) == {"A", "B", "C"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestBuildConsolidationPrompt backend/tests/test_location_normalizer.py::TestParseMergeMap backend/tests/test_location_normalizer.py::TestApplyMerges -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现**

```python
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
            continue  # 自合并
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

    # Union-Find：把所有 merge 对合并成 group
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
            # 保留 chapter_count 大的作为 root（让 root 名更"重要"）
            ra_cnt = next((c["chapter_count"] for c in all_canonicals if c["canonical_name"] == ra), 0)
            rb_cnt = next((c["chapter_count"] for c in all_canonicals if c["canonical_name"] == rb), 0)
            if ra_cnt >= rb_cnt:
                parent_uf[rb] = ra
            else:
                parent_uf[ra] = rb

    for pair in merge_map["merges"]:
        if pair[0] in parent_uf and pair[1] in parent_uf:
            _union(pair[0], pair[1])

    # 按 group 合并字段
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestBuildConsolidationPrompt backend/tests/test_location_normalizer.py::TestParseMergeMap backend/tests/test_location_normalizer.py::TestApplyMerges -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/location_normalizer.py backend/tests/test_location_normalizer.py
git commit -m "feat(location_normalizer): consolidation prompt + parse_merge_map + apply_merges"
```

---

## Task 6: LocationNormalizer orchestration class

**Files:**
- Modify: `backend/services/location_normalizer.py`（追加 `LocationNormalizer` 类 + 持久化方法）
- Modify: `backend/tests/test_location_normalizer.py`（追加 mock LLM 集成测试）

**Interfaces:**
- `LocationNormalizer(config, output_dir, llm_client)`
  - `.run() -> bool` — 跑完整 4 子阶段，返回是否成功
  - 内部方法：`_run_phase_0a_batches`、`_run_phase_0a_consolidate`、`_run_phase_0b_batches`、`_run_phase_0b_dedupe`、`_persist_normalized_locations`、`_persist_normalized_spatial`、`_add_normalized_ref_to_chapters`、`_compute_chapter_mtimes_hash`、`_load_chapter_locations`、`_load_chapter_spatial`

- [ ] **Step 1: 追加测试**

```python
import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.location_normalizer import LocationNormalizer


def _setup_output_dir(tmp_path: Path, chapters: int = 5):
    """写 N 个最小 chapter_*.json"""
    (tmp_path / "output").mkdir(exist_ok=True)
    for ch in range(1, chapters + 1):
        data = {
            "chapter_number": ch,
            "core_events": [],
            "character_arcs":":[],",
            "foreshadowing":":[],",
            "plot_holes":":[],",
            "locations": [
                {"name": "宁安县", "parent": "大周王朝", "type": "城市", "description": "测试县城"}
            ] if ch == 1 else [
                {"name": "宁安县城", "parent": "大周王朝", "type": "城池", "description": ""}
            ],
            "spatial_relationships":":[
                {"from": "宁安县", "to": "德胜府", "relation": "相邻", "chapter": ch}
            ] if ch <= 3 else [],
        }
        (tmp_path / "output" / f"chapter_{ch}_result.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )


def _make_mock_llm(responses: list[str]):
    """构造 mock LLM client，按调用顺序返回 responses"""
    client = MagicMock()
    client.chat = AsyncMock(side_effect=responses)
    return client


class TestLocationNormalizerRun:
    def test_runs_all_four_phases_and_writes_outputs(self, tmp_path):
        _setup_output_dir(tmp_path, chapters=3)

        # Phase 0a batch 返回：归一化宁安县 + 宁安县城 → "宁安县"
        # Phase 0b batch 返回：宁安县→德胜府
        responses = [
            json.dumps({"locations": [
                {"canonical_name": "宁安县", "aliases": ["宁安县", "宁安县城"], "parent": "大周王朝", "type": "城市", "description": "测试"}
            ]}, ensure_ascii=False),
            json.dumps({"relationships": [
                {"from": "宁安县", "to": "德胜府", "direction": "东南", "distance_text": "约两三百里", "distance_estimate_km": 130, "relation_type": "相邻", "evidence_chapters": [1, 2, 3]}
            ]}, ensure_ascii=False),
        ]
        mock_llm = _make_mock_llm(responses)

        norm = LocationNormalizer(
            output_dir=tmp_path,
            llm_client=mock_llm,
            concurrency=2,
        )
        result = asyncio.run(norm.run())
        assert result is True

        # 验证落盘
        assert (tmp_path / "output" / "locations_normalized.json").exists()
        assert (tmp_path / "output" / "spatial_relationships_normalized.json").exists()
        # 验证 _normalized_ref 加到 chapter_*.json
        for ch in range(1, 4):
            data = json.loads((tmp_path / "output" / f"chapter_{ch}_result.json").read_text(encoding="utf-8"))
            assert data.get("_normalized_ref") == "locations_normalized.json"
            assert data.get("_normalized_spatial_ref") == "spatial_relationships_normalized.json"

    def test_returns_false_when_phase_fails(self, tmp_path):
        _setup_output_dir(tmp_path, chapters=3)
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=Exception("LLM 故障"))

        norm = LocationNormalizer(output_dir=tmp_path, llm_client=mock_llm, concurrency=1)
        result = asyncio.run(norm.run())
        assert result is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestLocationNormalizerRun -v`
Expected: FAIL — `ImportError: cannot import name 'LocationNormalizer'`

- [ ] **Step 3: 实现 `LocationNormalizer` 类（追加到 location_normalizer.py 末尾）**

```python
import asyncio
import hashlib
import time
from dataclasses import replace as _replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import aiofiles  # noqa: F401  （如未用则删除）  # placeholder to avoid unused import
from backend.config.settings import AppConfig
from backend.core.llm_client import LLMClient
from backend.utils.json_utils import safe_load_json, safe_save_json


_LOCATIONS_NORMALIZED_FILENAME = "locations_normalized.json"
_SPATIAL_NORMALIZED_FILENAME = "spatial_relationships_normalized.json"
_CHECKPOINT_DIR = "final_summary_checkpoint"
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
        whitelist_names = {c["canonical_name"] for c in final_locations}
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

    def _load_all_chapter_data(self) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """从 output_dir/chapter_*.json 聚合 locations + spatial_relationships"""
        raw_locations: List[Dict[str, Any]] = []
        raw_spatial: List[Dict[str, Any]] = []
        output_subdir = self.output_dir / "output"
        if not output_subdir.is_dir():
            output_subdir = self.output_dir
        for cf in sorted(output_subdir.glob("chapter_*_result.json")):
            data = safe_load_json(cf)
            if not data:
                continue
            ch = data.get.get("chapter_number")
            if ch is None:
                continue
            for loc in data.get.get("locations", []) or []:
                loc_copy = dict(loc)
                loc_copy["chapter"] = ch
                raw_locations.append(loc_copy)
            for rel in data.get.get("spatial_relationships", []) or []:
                rel_copy = dict(rel)
                rel_copy["chapter"] = ch
                raw_spatial.append(rel_copy)
        return raw_locations, raw_spatial

    # ---- Phase 0a ----

    async def _run_phase_0a_batches(
        self, groups: List[Dict[str, Any]]
    ) -> Optional[List[Dict[str, Any]]]:
        batches = self._split_into_batches(groups, self.locations_batch_size)
        if not batches:
            return []
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _process(batch_idx: int, batch: List[Dict[str, Any]]):
            async with semaphore:
                messages = build_location_prompt(batch, batch_idx, len(batches))
                try:
                    response = await self.llm_client.chat(messages)
                except Exception as e:
                    logger.error(f"Phase 0a batch {batch_idx} 失败: {e}")
                    return None
                return parse_and_validate_locations(response, batch)

        tasks = [_process(i, b) for i, b in enumerate(batches, 1)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        all_canonicals: List[Dict[str, Any]] = []
        for r in results:
            if r is None:
                return None
            all_canonicals.extend(r)
        return all_canonicals

    async def _run_phase_0a_consolidate(
        self, all_canonicals: List[Dict[str, Any]]
    ) -> Optional[List[Dict[str, Any]]]:
        if len(all_canonicals) <= 2:
            return all_canonicals
        canonical_list = [
            {"canonical": c["canonical_name"], "aliases": c["aliases"]}
            for c in all_canonicals
        ]
        valid_names = {c["canonical"] for c in canonical_list}
        all_merges: List[List[str]] = []
        for i in range(0, len(canonical_list), _CONSOL_BATCH_SIZE):
            batch = canonical_list[i:i + _CONSOL_BATCH_SIZE]
            messages = build_consolidation_prompt(batch, i // _CONSOL_BATCH_SIZE + 1,
                                                  (len(canonical_list) + _CONSOL_BATCH_SIZE - 1) // _CONSOL_BATCH_SIZE)
            try:
                response = await self.llm_client.chat(messages)
            except Exception as e:
                logger.error(f"Phase 0a consol 失败: {e}")
                return None
            partial = parse_merge_map(response, valid_names)
            all_merges.extend(partial["merges"])
        return apply_merges(all_canonicals, {"merges": all_merges})

    # ---- Phase 0b ----

    async def _run_phase_0b_batches(
        self,
        pairs: List[Dict[str, Any]],
        whitelist: List[Dict[str, Any]],
        whitelist_names: Set[str],
    ) -> Optional[List[Dict[str, Any]]]:
        if not pairs:
            return []
        batches = self._split_into_batches(pairs, self.spatial_batch_size)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _process(batch_idx: int, batch: List[Dict[str, Any]]):
            async with semaphore:
                messages = build_spatial_prompt(batch, whitelist, batch_idx, len(batches))
                try:
                    response = await self.llm_client.chat(messages)
                except Exception as e:
                    logger.error(f"Phase 0b batch {batch_idx} 失败: {e}")
                    return None
                return parse_and_validate_spatial(response, whitelist_names)

        tasks = [_process(i, b) for i, b in enumerate(batches, 1)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        all_spatial: List[Dict[str, Any]] = []
        for r in results:
            if r is None:
                return None
            all_spatial.extend(r)
        return all_spatial

    @staticmethod
    def _run_phase_0b_dedupe(all_spatial: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """同 (from, to) 对的多 batch 结果合并：evidence_chapters 取并集"""
        pair_meta: Dict[tuple, Dict[str, Any]] = {}
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
    def _split_into_batches(items: List[Any], batch_size: int) -> List[List[Any]]:
        return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

    # ---- 持久化 ----

    def _persist_normalized_locations(self, locations: List[Dict[str, Any]]) -> None:
        output_subdir = self.output_dir / "output"
        if not output_subdir.is_dir():
            output_subdir = self.output_dir
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

    def _persist_normalized_spatial(self, spatial: List[Dict[str, Any]]) -> None:
        output_subdir = self.output_dir / "output"
        if not output_subdir.is_dir():
            output_subdir = self.output_dir
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
        output_subdir = self.output_dir / "output"
        if not output_subdir.is_dir():
            output_subdir = self.output_dir
        mtimes = []
        for cf in sorted(output_subdir.glob("chapter_*_result.json")):
            mtimes.append(f"{cf.name}:{int(cf.stat().st_mtime)}")
        return hashlib.md5("\n".join(mtimes).encode("utf-8")).hexdigest()

    def _add_normalized_ref_to_chapters(self) -> None:
        """给每章 chapter_*.json 顶部加 _normalized_ref + _normalized_spatial_ref 字段"""
        output_subdir = self.output_dir / "output"
        if not output_subdir.is_dir():
            output_subdir = self.output_dir
        for cf in output_subdir.glob("chapter_*_result.json"):
            data = safe_load_json(cf)
            if not data:
                continue
            data["_normalized_ref"] = _LOCATIONS_NORMALIZED_FILENAME
            data["_normalized_spatial_ref"] = _SPATIAL_NORMALIZED_FILENAME
            safe_save_json(data, cf)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestLocationNormalizerRun -v`
Expected: 2 passed

- [ ] **Step 5: 跑全套测试确认无回归**

Run: `python -m pytest backend/tests/test_location_normalizer.py -v`
Expected: 全部通过（前 5 任务 33 测试 + 本任务 2 测试）

- [ ] **Step 6: Commit**

```bash
git add backend/services/location_normalizer.py backend/tests/test_location_normalizer.py
git commit -m "feat(location_normalizer): LocationNormalizer class with 4-phase orchestration"
```

---

## Task 7: Wire into FinalSummaryRunner

**Files:**
- Modify: `backend/services/final_summary.py`（`__init__` 构造 + `run()` 调用 + 新方法 `_normalize_phase_0`）
- Modify: `backend/tests/test_summary_checkpoint.py`（追加 phase 0 调用测试）

**Interfaces:**
- `FinalSummaryRunner.run()` 在 `_init_progress()` 之后、`_run_batch()` 之前调用 `await self._normalize_phase_0()`
- 新方法 `_normalize_phase_0()` 构造 `LocationNormalizer` 并 await `.run()`

- [ ] **Step 1: 追加测试**

```python
# 添加到 test_summary_checkpoint.py 末尾

class TestPhase0Integration:
    def test_run_calls_normalizer_before_batch(self, tmp_path):
        _write_results(tmp_path, chapters=4)

        normalizer_mock = MagicMock()
        normalizer_mock.run = AsyncMock(return_value=True)

        with patch("backend.services.final_summary.LocationNormalizer") as NL:
            NL.return_value = normalizer_mock
            runner = FinalSummaryRunner(
                config=AppConfig(),
                output_dir=tmp_path,
                start_chapter=1,
                end_chapter=4,
                batch_size=2,
                concurrency=1,
            )
            # 不实际跑完整个 run（避免 LLM 调用），只验证 normalizer 被调用
            runner._run_batch = AsyncMock(return_value=None)
            runner._recheck_remaining = AsyncMock(return_value=None)
            runner._run_style_extraction = AsyncMock(return_value=None)
            runner._write_report = AsyncMock(return_value=None)
            runner._emit_progress = MagicMock()

            asyncio.run(runner.run())

            assert normalizer_mock.run.called

    def test_phase0_failure_marks_summary_failed(self, tmp_path):
        _write_results(tmp_path, chapters=4)

        normalizer_mock = MagicMock()
        normalizer_mock.run = AsyncMock(return_value=False)

        with patch("backend.services.final_summary.LocationNormalizer") as NL:
            NL.return_value = normalizer_mock
            runner = FinalSummaryRunner(
                config=AppConfig(),
                output_dir=tmp_path,
                start_chapter=1,
                end_chapter=4,
                batch_size=2,
                concurrency=1,
            )
            runner._run_batch = AsyncMock(return_value=None)
            runner._emit_progress = MagicMock()

            asyncio.run(runner.run())

            # Phase 1 不应被调用
            assert not runner._run_batch.called
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_summary_checkpoint.py::TestPhase0Integration -v`
Expected: FAIL — `AttributeError: type object 'FinalSummaryRunner' has no attribute '_normalize_phase_0'`

- [ ] **Step 3: 修改 final_summary.py**

**3a. 在文件顶部 import 区域追加：**

```python
from backend.services.location_normalizer import LocationNormalizer
```

**3b. 在 `__init__` 末尾追加 self.xxx 字段：**

在 `self.book_name = detect_book_name(...)` 行附近追加：
```python
        # Phase 0 归一化器（不立即启动，run() 内按需触发）
        self._normalizer: Optional[LocationNormalizer] = None
```

**3c. 在 `run()` 方法里找到 `_init_progress()` 调用行，在其后插入：**

```python
        # Phase 0: 地点 + 空间关系归一化（LLM 调用，token 计入 summary）
        if not await self._normalize_phase_0():
            logger.error("Phase 0 归一化失败，终止总结")
            self._emit_progress({"type": "complete", "status": "failed"})
            return
```

**3d. 在类内任意位置（建议 `stop()` 之后）新增方法：**

```python
    async def _normalize_phase_0(self) -> bool:
        """Phase 0：调用 LocationNormalizer 跑完整 4 子阶段"""
        try:
            if self._normalizer is None:
                self._normalizer = LocationNormalizer(
                    output_dir=self.output_dir,
                    llm_client=self._llm,
                    concurrency=self.concurrency,
                )
            return await self._normalizer.run()
        except Exception as e:
            logger.error(f"Phase 0 归一化异常: {e}", exc_info=True)
            return False
```

- [ ] **Step 4: 跑新测试确认通过**

Run: `python -m pytest backend/tests/test_summary_checkpoint.py::TestPhase0Integration -v`
Expected: 2 passed

- [ ] **Step 5: 跑全套确保无回归**

Run: `python -m pytest backend/tests/test_summary_checkpoint.py -v`
Expected: 全部通过（旧 4 测试 + 新 2 测试）

- [ ] **Step 6: Commit**

```bash
git add backend/services/final_summary.py backend/tests/test_summary_checkpoint.py
git commit -m "feat(final_summary): wire LocationNormalizer as Phase 0"
```

---

## Task 8: queue_service 集成检查

**Files:**
- Modify: `backend/services/queue_service.py`（仅校验，不改代码除非必要）

**Interfaces:** 无新增；仅验证 FinalSummaryRunner 创建链路已经覆盖 LocationNormalizer（Task 7 已完成）

- [ ] **Step 1: 检查 queue_service.py 中所有创建 FinalSummaryRunner 的位置**

Run:
```bash
grep -n "FinalSummaryRunner(" backend/services/queue_service.py
```

**Expected**: 找到 1-3 处 `FinalSummaryRunner(...)` 调用。**不需要修改**（Task 7 已在 `__init__` 内自动构造 LocationNormalizer，queue_service 不需要知道这件事）。

- [ ] **Step 2: 跑现有 queue_service 测试确认无影响**

Run: `python -m pytest backend/tests/test_queue_service.py -v`
Expected: 全部通过

- [ ] **Step 3: 跑全量 backend 测试确认无回归**

Run: `python -m pytest backend/tests/ -v`
Expected: 全部通过（101 + 新增测试）

- [ ] **Step 4: Commit（如有改动则提）**

如果 Step 1 显示需要改，按需 commit；如不需要，记录 "无需改动" 即可。

---

## Task 9: viz_service.map_data 归一化数据读取

**Files:**
- Modify: `backend/services/viz_service.py`（`map_data` 加归一化分支）
- Modify: `backend/tests/test_location_normalizer.py`（追加 viz 读取测试）

**Interfaces:**
- `map_data(output_dir)` — 检测到 `output/locations_normalized.json` 且任一 chapter_*.json 含 `_normalized_ref` → 读归一化数据；否则走老逻辑

- [ ] **Step 1: 追加测试**

```python
from backend.services.viz_service import map_data


class TestMapDataNormalized:
    def test_returns_normalized_data_when_ref_exists(self, tmp_path):
        (tmp_path / "output").mkdir()
        # 写一个 chapter_*.json 含 _normalized_ref
        chapter = {"_normalized_ref": "locations_normalized.json",
                   "_normalized_spatial_ref": "spatial_relationships_normalized.json",
                   "locations":":[]," "spatial_relationships":[]}
        (tmp_path / "output" / "chapter_1_result.json").write_text(
            json.dumps(chapter, ensure_ascii=False), encoding="utf-8"
        )
        # 写归一化文件
        normalized = {
            "schema_version": 1,
            "locations": [
                {"canonical_name": "宁安县", "aliases": ["宁安县", "宁安县城"],
                 "parent": "大周王朝", "type": "城市", "description": "测试", "chapter_count": 5}
            ],
        }
        (tmp_path / "output" / "locations_normalized.json").write_text(
            json.dumps(normalized, ensure_ascii=False), encoding="utf-8"
        )
        spatial = {"schema_version": 1, "relationships": [
            {"from": "宁安县", "to": "德胜府", "direction": "东南",
             "distance_text": "约两三百里", "distance_estimate_km": 130,
             "relation_type": "相邻", "evidence_chapters": [1, 2]}
        ]}
        (tmp_path / "output" / "spatial_relationships_normalized.json").write_text(
            json.dumps(spatial, ensure_ascii=False), encoding="utf-8"
        )

        result = map_data(tmp_path)
        assert len(result["locations"]) == 1
        assert result["locations"][0]["name"] == "宁安县"
        assert "宁安县城" in result["locations"][0]["aliases"]
        assert len(result["relationships"]) == 1
        assert result["relationships"][0]["direction"] == "东南"

    def test_falls_back_to_old_logic_when_no_ref(self, tmp_path):
        (tmp_path / "output").mkdir()
        chapter = {
            "chapter_number": 1,
            "locations": [{"name": "宁安县", "parent": "", "type": "", "description": ""}],
            "spatial_relationships": []
        }
        (tmp_path / "output" / "chapter_1_result.json").write_text(
            json.dumps(chapter, ensure_ascii=False), encoding="utf-8"
        )

        result = map_data(tmp_path)
        assert len(result["locations"]) == 1
        assert result["locations"][0]["name"] == "宁安县"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestMapDataNormalized -v`
Expected: FAIL — `map_data` 仍走老逻辑，不读 normalized 文件

- [ ] **Step 3: 修改 viz_service.py 的 `map_data`**

把当前 `map_data` 方法替换为：

```python
def map_data(output_dir: Path) -> Dict[str, Any]:
    """地图数据：地点层级 + 空间关系

    检测归一化文件存在性：
    - 若任一 chapter_*.json 含 _normalized_ref → 读 normalized 文件（已归一化）
    - 否则走老逻辑（聚合 raw chapter_*.json）
    """
    output_subdir = output_dir / "output"
    if not output_subdir.is_dir():
        output_subdir = output_dir

    # 检测是否有归一化数据
    locations_normalized_path = output_subdir / "locations_normalized.json"
    spatial_normalized_path = output_subdir / "spatial_relationships_normalized.json"

    use_normalized = False
    if locations_normalized_path.exists() and spatial_normalized_path.exists():
        for cf in output_subdir.glob("chapter_*_result.json"):
            try:
                with open(cf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("_normalized_ref") == "locations_normalized.json":
                    use_normalized = True
                    break
            except Exception:
                continue

    if use_normalized:
        try:
            with open(locations_normalized_path, "r", encoding="utf-8") as f:
                loc_norm = json.load(f)
            with open(spatial_normalized_path, "r", encoding="utf-8") as f:
                rel_norm = json.load(f)
            return {
                "locations": [
                    {
                        "id": loc["canonical_name"],
                        "name": loc["canonical_name"],
                        "aliases": loc.get("aliases", []),
                        "parent": loc.get("parent", ""),
                        "type": loc.get("type", ""),
                        "description": loc.get("description", ""),
                        "chapters":":[],"  # 归一化文件不含详细章节数组
                    }
                    for loc in loc_norm.get("locations", [])
                ],
                "relationships": rel_norm.get("relationships", []),
            }
        except Exception as e:
            logger.warning(f"读归一化数据失败，回退到老逻辑: {e}")

    # 老逻辑（保持原实现不变）
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
                spatial_rels.append({"from": rel, "to": rel, "relation": rel.relation})

    return {
        "locations": list(locations.values()),
        "relationships": spatial_rels,
    }
```

**注意**：保留原 `timeline_data` 和 `_load_type_category_map` 函数不动；只替换 `map_data` 主体。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_location_normalizer.py::TestMapDataNormalized -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/viz_service.py backend/tests/test_location_normalizer.py
git commit -m "feat(viz_service): map_data reads normalized files when _normalized_ref present"
```

---

## Task 10: agent.md 更新

**Files:**
- Modify: `agent.md`（§5 加新模块 + §10 加变更记录）

**Interfaces:** 无（文档）

- [ ] **Step 1: §5 加 LocationNormalizer 说明**

在 `backend/services/viz_service.py` 段落附近（§5.2 services 层），追加一段：

```markdown
#### location_normalizer.py（420+行）
- **职责**：地点与空间关系 LLM 归一化（Phase 0）
- **关键类**：`LocationNormalizer`
- **4 子阶段**：
  1. `_run_phase_0a_batches`：locations 切片 + 并发 LLM 调 + 校验（canonical ∈ aliases）
  2. `_run_phase_0a_consolidate`：1+ 次 LLM 合并跨 batch 同地点
  3. `_run_phase_0b_batches`：spatial 切片 + canonical 白名单 + 并发 LLM
  4. `_run_phase_0b_dedupe`：机械去重同 (from, to) 对
- **触发**：`FinalSummaryRunner.run()` 内自动调用（`_init_progress` 之后）
- **复用配置**：`summary_model` / `summary_concurrency` / `summary_timeout` / `summary_thinking_mode`
- **输出**：`output/locations_normalized.json` + `output/spatial_relationships_normalized.json`
- **chapter 标记**：每章 chapter_*.json 顶部加 `_normalized_ref` + `_normalized_spatial_ref` 字段
```

- [ ] **Step 2: §10 加变更记录**

在 §10.10 graph bug 修复（图表白屏）之后插入：

```markdown
### 10.11 2026-08-21 地点 + 空间关系归一化（Phase 0）

**症状**：`MapPage.vue` 渲染树形布局时节点分散、parent 跳转、type 颜色不一致；spatial_relationships 无法按方向/距离过滤。

**根因**：LLM 生成 `chapter_*.json` 时 `locations.parent` 多达 8 种不同值（实测 130/889 地点）、`type` 多达 9 种不同值（143/889 地点）；`spatial_relationships.relation` 是自由文本散文（54 对边有多重不一致描述）。

**修复**（spec: `docs/superpowers/specs/2026-08-21-location-spatial-normalization-design.md`，plan: `docs/superpowers/plans/2026-08-21-location-spatial-normalization.md`）：
- 新增 `LocationNormalizer`（`backend/services/location_normalizer.py`，420+ 行）
- 在 `FinalSummaryRunner.run()` 最前面插入 4 子阶段流水线（0a-batches → 0a-consol → 0b-batches → 0b-dedupe）
- 输出 `output/locations_normalized.json` + `output/spatial_relationships_normalized.json`，非破坏性
- 每章 `chapter_*.json` 顶部加 `_normalized_ref` 字段，`viz_service.map_data()` 检测该字段决定读归一化数据
- 复用 `summary_model/summary_concurrency/summary_timeout/summary_thinking_mode` 配置，无 UI 改动
- 大书（千万字）通过 1000 group/batch + 2000 canonical/consol-batch 拆分避免 context 溢出
```

- [ ] **Step 3: Commit**

```bash
git add agent.md
git commit -m "docs(agent): §5/§10 sync 2026-08-21 Phase 0 normalization"
```

---

## Self-Review Checklist（写完后执行）

1. **Spec 覆盖检查**：spec 10 节每节都有对应 task
   - §1 触发与生命周期 → Task 6（LocationNormalizer.run）+ Task 7（FinalSummaryRunner 集成）
   - §2 数据契约 → Task 6（_persist_normalized_locations / _persist_normalized_spatial / _add_normalized_ref_to_chapters）
   - §3 4 子阶段 → Task 2-6
   - §4 配置复用 → Task 7（构造函数继承 config）+ 全程不新增配置
   - §5 文件改动清单 → Task 1（text_utils）/ Task 2-6（location_normalizer）/ Task 7（final_summary）/ Task 8（queue_service 检查）/ Task 9（viz_service）
   - §6 验证清单 → Task 6/9 测试
   - §7 注意事项 → 全文贯彻（不批量格式化 / 不动 MapPage.vue / 不动 aggregate_utils）
   - §8 数据流 → Task 6 实现
   - §9 决策记录 → 全部按 spec 决策落地
   - §10 未来工作 → 不在本 plan 范围

2. **Placeholder 扫描**：搜索"TBD/TODO/FIXME/XXX" → 应为 0
3. **Type 一致性**：`_run_phase_0a_batches` / `_run_phase_0b_batches` / `_run_phase_0a_consolidate` / `_run_phase_0b_dedupe` 在 Task 6 定义，Task 7 调用一致；`build_*_prompt` / `parse_and_validate_*` 在 Task 3-5 定义，Task 6 调用一致

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-21-location-spatial-normalization.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review

Which approach?