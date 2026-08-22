"""地点归一化编排服务测试（2026-08-22 spec）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.services.location_normalization_service import (
    LocationNormalizationService,
    get_location_normalization_service,
)


def test_status_initial_state():
    svc = LocationNormalizationService()
    s = svc.status()
    assert s["running"] is False
    assert s["phase"] == "idle"
    assert s["book_id"] == ""


def test_singleton_pattern():
    a = get_location_normalization_service()
    b = get_location_normalization_service()
    assert a is b