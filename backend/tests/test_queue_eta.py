"""ETA 计算与格式化单测

覆盖 _format_eta（边界 + 三种量级）和 _compute_eta（4 种前置条件：未开始、
刚开始、进行中、已完成）。后端 hub.progress(current, total, eta) 第三个
参数就是用 _compute_eta 算出来的，跑通这两个函数就保证 on_progress 转发
出去的 eta 字符串格式正确。
"""
import math
import pytest

from backend.services.queue_service import _format_eta, _compute_eta


# ===== _format_eta =====

class TestFormatEtaBoundaries:
    """边界 / 非法输入全部返回空串（让前端 RunDashboard 显示 '—'）"""

    def test_zero_returns_empty(self):
        assert _format_eta(0) == ""

    def test_negative_returns_empty(self):
        assert _format_eta(-1) == ""
        assert _format_eta(-100) == ""

    def test_inf_returns_empty(self):
        assert _format_eta(float("inf")) == ""

    def test_nan_returns_empty(self):
        # NaN != NaN 的特性可用于检测
        assert _format_eta(math.nan) == ""


class TestFormatEtaSeconds:
    """< 60s：单段"秒"，无前导零"""

    def test_one_second(self):
        assert _format_eta(1) == "1秒"

    def test_tens(self):
        assert _format_eta(30) == "30秒"

    def test_just_under_a_minute(self):
        assert _format_eta(59) == "59秒"

    def test_rounds_up(self):
        # 0.6 → round → 1
        assert _format_eta(0.6) == "1秒"


class TestFormatEtaMinutesSeconds:
    """1 分钟 ~ 1 小时："M 分 SS 秒"，秒数补 0 保持 2 位"""

    def test_exactly_one_minute(self):
        assert _format_eta(60) == "1分00秒"

    def test_one_and_a_half_minutes(self):
        assert _format_eta(90) == "1分30秒"

    def test_just_under_an_hour(self):
        assert _format_eta(3599) == "59分59秒"

    def test_ten_minutes(self):
        assert _format_eta(600) == "10分00秒"


class TestFormatEtaHoursMinutes:
    """≥ 1 小时：只显示"X 时 YY 分"，省去秒数（>1h 没必要精确到秒）"""

    def test_exactly_one_hour(self):
        assert _format_eta(3600) == "1时00分"

    def test_one_hour_one_minute(self):
        assert _format_eta(3660) == "1时01分"

    def test_two_hours(self):
        assert _format_eta(7200) == "2时00分"

    def test_24_hours(self):
        assert _format_eta(86400) == "24时00分"


# ===== _compute_eta =====

class TestComputeEtaGuards:
    """前置条件不满足时返回空串（让前端走默认 '—'）"""

    def test_no_start_time(self):
        # start_time=0（初始值）或负数：未开始
        assert _compute_eta(0.0, current=5, total=10, now=100.0) == ""

    def test_current_zero(self):
        # 刚启动、还没完成任何块：无法估算速率
        assert _compute_eta(50.0, current=0, total=10, now=55.0) == ""

    def test_current_equals_total(self):
        # 已完成：remaining=0 → 空串
        assert _compute_eta(50.0, current=10, total=10, now=60.0) == ""

    def test_current_exceeds_total(self):
        # 异常情况（重试导致进度倒退再恢复）：保守起见返回空串
        assert _compute_eta(50.0, current=11, total=10, now=60.0) == ""

    def test_negative_current(self):
        assert _compute_eta(50.0, current=-1, total=10, now=55.0) == ""


class TestComputeEtaLinear:
    """线性外推正确性

    注：start_time 在生产中是 time.time() 返回的 unix 时间戳（~1.7e9），
    测试用相对值（如 start_time=1000.0）以模拟"已运行若干秒"的场景；
    _compute_eta 的 guard 用 `if not start_time`（即 start_time <= 0）
    排除"未开始"语义，所以 start_time 必须 > 0。
    """

    def test_half_done_at_same_rate_doubles(self):
        # 100s 内完成 5/10 块 → 剩余 100s
        result = _compute_eta(start_time=1000.0, current=5, total=10, now=1100.0)
        assert result == "1分40秒"  # 100 秒

    def test_quarter_done_quadruples_remaining(self):
        # 60s 内完成 10/40 块 → 剩余 180s
        result = _compute_eta(start_time=1000.0, current=10, total=40, now=1060.0)
        assert result == "3分00秒"  # 180 秒

    def test_fast_completion_yields_short_eta(self):
        # 10s 内完成 99/100 块 → 剩余 10/99 ≈ 0.1s → 向上取整 0s → 空串
        result = _compute_eta(start_time=1000.0, current=99, total=100, now=1010.0)
        assert result == ""  # 剩余 < 0.5s 时向上取整为 0 → 空串

    def test_long_eta_formats_as_hours(self):
        # 60s 内完成 1/10 块 → 剩余 540s
        result = _compute_eta(start_time=1000.0, current=1, total=10, now=1060.0)
        # 540s = 9 min 0 sec
        assert result == "9分00秒"

    def test_very_long_eta_formats_as_hours(self):
        # 60s 内完成 1/100 块 → 剩余 5940s = 1h 39min
        result = _compute_eta(start_time=1000.0, current=1, total=100, now=1060.0)
        assert result == "1时39分"


class TestComputeEtaEdge:
    """极端时间值不爆炸"""

    def test_tiny_elapsed_doesnt_div_by_zero(self):
        # now - start_time = 0 时，elapsed 被 max(_, 0.1) 兜底
        result = _compute_eta(start_time=100.0, current=1, total=10, now=100.0)
        # elapsed=0.1, rate=10, remaining=(9)/10 = 0.9s → round → 1s
        assert result == "1秒"

    def test_slow_rate_yields_long_eta(self):
        # 1s 内完成 1/100 块 → remaining ≈ 99s
        result = _compute_eta(start_time=1000.0, current=1, total=100, now=1001.0)
        assert result == "1分39秒"  # 99 秒
