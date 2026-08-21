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


import json

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