"""安全验证 splitter 的批量切分核心逻辑（不污染真实工作区）。"""
import json
import tempfile
import shutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services import splitter_service as ss

SAMPLE = """第一卷 风起
第一章 初入江湖
这是第一章的内容，主角踏上了旅程。
第二章 偶遇高人
在山林里遇到了一位隐世高人，传授武功。
第二卷 浪涌
第三章 风波乍起
江湖中掀起了腥风血雨，主角被迫卷入。
第四章 决战之时
最终一战，主角胜出，江湖重归平静。
"""

def main():
    tmp = Path(tempfile.mkdtemp(prefix="splitter_test_"))
    try:
        src = tmp / "测试小说.txt"
        src.write_text(SAMPLE, encoding="utf-8")
        ws = tmp / "workspace"

        opts = ss.SplitOptions(use_volume=True, remove_ads=False, merge_tiny=False)
        r = ss.save_to_workspace(src, ws, "测试小说", options=opts)
        print("save_to_workspace ->", json.dumps({k: r[k] for k in ("book_name", "total_chapters", "total_volumes")}, ensure_ascii=False))

        blocks = sorted((ws / "测试小说" / "blocks").glob("[0-9][0-9][0-9][0-9].txt"))
        print("chapter blocks count:", len(blocks))
        assert r["total_chapters"] == 4, f"期望 4 章, 实际 {r['total_chapters']}"
        assert r["total_volumes"] == 2, f"期望 2 卷, 实际 {r['total_volumes']}"
        assert len(blocks) == 4, f"期望 4 个 block 文件, 实际 {len(blocks)}"
        # 验证卷归属存进 chapters.json（卷是结构元数据，不污染章节正文）
        chapters = json.loads((ws / "测试小说" / "blocks" / "chapters.json").read_text(encoding="utf-8"))
        vol_of = {c["index"]: c.get("volume") for c in chapters}
        assert vol_of[1] == "第一卷" and vol_of[2] == "第一卷", "前 2 章应归属第一卷"
        assert vol_of[3] == "第二卷" and vol_of[4] == "第二卷", "后 2 章应归属第二卷"
        print("PASSED: 切分核心 + 卷层级 + 写入均正常")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
