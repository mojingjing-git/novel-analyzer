"""
测试 GET /api/books/{book_id}/token_stats 端点
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient
from backend.api.routes_books import router
from fastapi import FastAPI


app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_get_book_token_stats_reads_both_files(tmp_path):
    """两个文件都存在时，返回 analysis + summary 两个键"""
    (tmp_path / "token_stats.json").write_text(
        '{"type":"analysis","categories":{"chapter":{"input_tokens":100,"output_tokens":50}}}',
        encoding="utf-8")
    (tmp_path / "summary_token_stats.json").write_text(
        '{"type":"summary","categories":{"summary":{"input_tokens":10,"output_tokens":5}}}',
        encoding="utf-8")

    with patch("backend.api.routes_books.book_service.get_output_dir", return_value=tmp_path):
        resp = client.get("/api/books/test_book/token_stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["book_id"] == "test_book"
    assert "analysis" in data
    assert "summary" in data
    assert data["analysis"]["categories"]["chapter"]["input_tokens"] == 100
    assert data["summary"]["categories"]["summary"]["output_tokens"] == 5
    print("✅ test_get_book_token_stats_reads_both_files passed")


def test_get_book_token_stats_404_for_unknown_book():
    """get_output_dir 返回 None → 404"""
    with patch("backend.api.routes_books.book_service.get_output_dir", return_value=None):
        resp = client.get("/api/books/unknown_book/token_stats")
    assert resp.status_code == 404
    print("✅ test_get_book_token_stats_404_for_unknown_book passed")
