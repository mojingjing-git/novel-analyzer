"""验证 /api/splitter/batch 端点走通成功路径（临时文件 + 临时书名 + 清理）。"""
import json
import shutil
import tempfile
import urllib.request
from pathlib import Path

SAMPLE = "第一章 测试\n这是测试内容。\n第二章 结束\n结束内容。\n"

def main():
    tmp = Path(tempfile.mkdtemp(prefix="batch_http_", dir=str(Path.cwd())))
    src = tmp / "test_novel.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    book_name = f"_batch_http_test_{src.stat().st_mtime_ns}"

    body = json.dumps({
        "file_paths": [str(src)],
        "book_names": [book_name],
        "mode": "auto",
        "pattern": "",
        "use_volume": True,
        "min_words": 0,
        "merge_tiny": False,
        "max_words": 0,
        "remove_ads": False,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/splitter/batch",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        res = urllib.request.urlopen(req)
        data = json.loads(res.read().decode("utf-8"))
        print(json.dumps(data, ensure_ascii=False, indent=2))
        assert data["total"] == 1, "total should be 1"
        assert data["success"] == 1, f"success should be 1, got {data['success']}"
        assert data["results"][0]["ok"] is True, "result should be ok"
        print("PASSED: HTTP batch endpoint success path works")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        ws = Path("workspace") / book_name
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
            print("cleaned workspace entry:", ws)

if __name__ == "__main__":
    main()
