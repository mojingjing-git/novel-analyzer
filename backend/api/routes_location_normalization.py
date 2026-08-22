"""
地点归一化路由
POST /api/location-normalization/start  — 启动指定书的地点归一化
POST /api/location-normalization/stop   — 停止当前归一化任务
GET  /api/location-normalization/status — 查询状态
GET  /api/location-normalization/result/{book_id} — 读取已归一化数据
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.location_normalization_service import get_location_normalization_service
from backend.services.book_service import get_output_dir
from backend.utils.json_utils import safe_load_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/location-normalization", tags=["location-normalization"])


class LocationNormalizationStartRequest(BaseModel):
    book_id: str


def _load_normalized_summary(output_dir: Path) -> Optional[dict]:
    """读 normalized_locations.json 摘要"""
    loc_path = output_dir / "output" / "locations_normalized.json"
    rel_path = output_dir / "output" / "spatial_relationships_normalized.json"
    if not loc_path.exists() or not rel_path.exists():
        return None
    loc_data = safe_load_json(loc_path) or {}
    rel_data = safe_load_json(rel_path) or {}
    locs = (loc_data.get("locations") or [])
    rels = (rel_data.get("relationships") or [])
    return {
        "normalized_at": loc_data.get("normalized_at"),
        "model": loc_data.get("model"),
        "chapter_mtimes_hash": loc_data.get("chapter_mtimes_hash"),
        "location_count": len(locs),
        "spatial_count": len(rels),
    }


@router.get("/status")
async def status() -> dict:
    return get_location_normalization_service().status()


@router.post("/start")
async def start(req: LocationNormalizationStartRequest) -> dict:
    from backend.config.settings import AppConfig
    from backend.services.queue_service import get_service as get_analysis_service

    svc = get_location_normalization_service()
    try:
        config: AppConfig = get_analysis_service().config_manager.load()
        svc.start(book_id=req.book_id, config=config)
        return {"ok": True}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/stop")
async def stop() -> dict:
    if not get_location_normalization_service().stop():
        raise HTTPException(status_code=400, detail="没有正在运行的归一化任务")
    return {"ok": True}


@router.get("/result/{book_id}")
async def result(book_id: str) -> dict:
    output_dir = get_output_dir(book_id)
    if output_dir is None:
        raise HTTPException(status_code=404, detail=f"书目不存在: {book_id}")
    summary = _load_normalized_summary(output_dir)
    if summary is None:
        return {"exists": False}
    return {"exists": True, **summary}
