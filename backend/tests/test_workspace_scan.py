"""
测试归档目录（workspace/分析结果/）中的书可被识别：
1. book_service.refresh_books 应发现归档目录下的书
2. workspace_service.list_archives 应指向 workspace/分析结果（与 queue_service 归档位置一致）
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.services import book_service, workspace_service


class _FakeQueue:
    items = []


class _FakeConfig:
    working_directory = None


class _FakeConfigManager:
    def __init__(self):
        self.config = _FakeConfig()


class _FakeService:
    def __init__(self, workspace: Path):
        self.queue = _FakeQueue()
        self.config_manager = _FakeConfigManager()
        self.workspace_path = workspace


def _make_workspace(root: Path) -> Path:
    """构造 workspace：根下有《书A》，归档目录下有《书B》"""
    ws = root / "workspace"
    book_a = ws / "《书A》"
    (book_a / "blocks").mkdir(parents=True)
    (book_a / "blocks" / "1.txt").write_text("内容", encoding="utf-8")
    archive = ws / "分析结果"
    book_b = archive / "《书B》"
    (book_b / "blocks").mkdir(parents=True)
    (book_b / "blocks" / "1.txt").write_text("内容", encoding="utf-8")
    return ws


def test_refresh_books_finds_archived_books():
    """队列空 + working_directory 空时，归档目录下的书也应被识别"""
    with tempfile.TemporaryDirectory() as td:
        ws = _make_workspace(Path(td))
        fake = _FakeService(ws)
        with patch("backend.services.book_service.get_service", return_value=fake):
            books = book_service.refresh_books()
        names = set(books.keys())
        assert "《书A》" in names, f"根目录下的书未被识别: {names}"
        assert "《书B》" in names, f"归档目录下的书未被识别: {names}"
        assert books["《书B》"] == ws / "分析结果" / "《书B》"


def test_list_archives_uses_workspace_archive_root():
    """归档根应在 workspace/分析结果（与 queue_service 归档位置一致），而非 workspace 同级"""
    with tempfile.TemporaryDirectory() as td:
        ws = _make_workspace(Path(td))
        fake = _FakeService(ws)
        with patch("backend.services.workspace_service.get_service", return_value=fake):
            archives = workspace_service.list_archives()
        names = [a["name"] for a in archives]
        assert "《书B》" in names, f"归档未被列出: {names}"
