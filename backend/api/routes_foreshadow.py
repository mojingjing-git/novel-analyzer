"""
伏笔分类元数据路由
GET /api/foreshadow/categories — 返回 50 类定义 + 用户当前保留列表
供前端 TimelinePage 筛选器、SettingsPage 复选框共享同一份元数据
"""

import logging

from fastapi import APIRouter

from backend.config.constants import (
    FORESHADOW_CATEGORY_DEFS,
    FORESHADOW_CATEGORY_SCHEMA_VERSION,
    FORESHADOW_CATEGORY_FALLBACK,
)
from backend.services.queue_service import get_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/foreshadow", tags=["foreshadow"])


@router.get("/categories")
async def get_foreshadow_categories() -> dict:
    service = get_service()
    config = service.config_manager.load()
    defs = [
        {"name": name, "description": desc, "examples": list(anchors)}
        for name, desc, anchors in FORESHADOW_CATEGORY_DEFS
    ]
    return {
        "schema_version": FORESHADOW_CATEGORY_SCHEMA_VERSION,
        "fallback": FORESHADOW_CATEGORY_FALLBACK,
        "defs": defs,
        "kept": list(config.analysis.foreshadow_kept_categories),
        "min_importance": config.analysis.foreshadow_min_importance,
        "min_confidence": config.analysis.foreshadow_min_confidence,
    }
