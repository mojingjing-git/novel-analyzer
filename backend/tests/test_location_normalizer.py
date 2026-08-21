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