"""
工作区管理路由
GET  /api/workspace/novels      — 列出 workspace 中的小说
GET  /api/workspace/archives    — 列出已有归档
POST /api/workspace/archive     — 归档指定小说
POST /api/workspace/archive_all — 归档全部
POST /api/workspace/delete_archive — 删除归档
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services import workspace_service
from backend.services.queue_service import get_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def _require_analysis_idle():
    """归档会 rename/move workspace 内正在分析的小说目录，导致管线路径失效，故分析运行时拒绝。"""
    if get_service().is_running:
        raise HTTPException(status_code=409, detail="分析正在进行中，请先停止或等待其完成后再归档")


class ArchiveRequest(BaseModel):
    novel_name: str
    mode: str = "rename"  # skip / overwrite / rename


class DeleteArchiveRequest(BaseModel):
    archive_name: str


@router.get("/novels")
async def list_novels():
    """列出 workspace 中的小说目录"""
    return {"novels": workspace_service.list_workspace_novels()}


@router.get("/archives")
async def list_archives():
    """列出 分析结果/ 中的归档"""
    return {"archives": workspace_service.list_archives()}


@router.post("/archive")
async def archive(req: ArchiveRequest):
    """归档指定小说"""
    _require_analysis_idle()
    if req.mode not in ("skip", "overwrite", "rename"):
        raise HTTPException(status_code=422, detail="mode 须为 skip/overwrite/rename")
    result = workspace_service.archive_novel(req.novel_name, mode=req.mode)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "归档失败"))
    return result


@router.post("/archive_all")
async def archive_all():
    """归档全部小说"""
    _require_analysis_idle()
    return workspace_service.archive_all()


@router.post("/delete_archive")
async def delete_archive(req: DeleteArchiveRequest):
    """删除指定归档"""
    result = workspace_service.delete_archive(req.archive_name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "删除失败"))
    return result
