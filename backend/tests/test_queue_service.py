"""
测试 QueueItem 和 QueueManager 的增删改/持久化/恢复
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.services.queue_service import (
    QueueItem, QueueManager, AnalysisService,
    _extract_discovery, _DISCOVERY_STOP_WORDS,
)


def test_queue_item_roundtrip():
    """QueueItem 序列化往返"""
    item = QueueItem(
        name="测试小说",
        blocks_dir=Path("/workspace/测试/blocks"),
        workspace_dir=Path("/workspace/测试"),
        status="done",
        total_chapters=100,
        completed_chapters=80,
        block_size=3,
        start_time=1000.0,
        end_time=2000.0,
        error_message="",
        archive_failed=False,
    )
    d = item.to_dict()
    assert d["name"] == "测试小说"
    assert d["status"] == "done"
    assert d["total_chapters"] == 100

    item2 = QueueItem.from_dict(d)
    assert item2.name == item.name
    assert item2.status == item.status
    assert item2.total_chapters == item.total_chapters
    assert item2.completed_chapters == item.completed_chapters
    print("✅ test_queue_item_roundtrip passed")


def test_queue_manager_add_remove():
    """队列添加和移除"""
    qm = QueueManager()
    item1 = QueueItem(name="小说A", blocks_dir=Path("/a/blocks"), workspace_dir=Path("/a"))
    item2 = QueueItem(name="小说B", blocks_dir=Path("/b/blocks"), workspace_dir=Path("/b"))
    item3 = QueueItem(name="小说C", blocks_dir=Path("/c/blocks"), workspace_dir=Path("/c"))

    qm.add_item(item1)
    qm.add_item(item2)
    qm.add_item(item3)
    assert qm.count == 3

    removed = qm.remove_item(1)
    assert removed is not None
    assert removed.name == "小说B"
    assert qm.count == 2
    assert qm.items[0].name == "小说A"
    assert qm.items[1].name == "小说C"
    print("✅ test_queue_manager_add_remove passed")


def test_queue_manager_dedup():
    """队列去重"""
    qm = QueueManager()
    item1 = QueueItem(name="小说A", blocks_dir=Path("/a/blocks"), workspace_dir=Path("/a"))
    item2 = QueueItem(name="小说A重复", blocks_dir=Path("/a/blocks"), workspace_dir=Path("/a"))
    qm.add_item(item1)
    idx = qm.add_item(item2)
    assert qm.count == 1
    assert idx == 0
    print("✅ test_queue_manager_dedup passed")


def test_queue_manager_move():
    """队列上移下移"""
    qm = QueueManager()
    for i in range(4):
        qm.add_item(QueueItem(name=f"小说{i}", blocks_dir=Path(f"/{i}/blocks"), workspace_dir=Path(f"/{i}")))

    assert qm.move_up(2)  # [0,2,1,3]
    assert qm.items[1].name == "小说2"
    assert qm.items[2].name == "小说1"

    assert qm.move_down(0)  # [2,0,1,3]
    assert qm.items[0].name == "小说2"
    assert qm.items[1].name == "小说0"

    assert not qm.move_up(0)  # can't move first up
    assert not qm.move_down(3)  # can't move last down
    print("✅ test_queue_manager_move passed")


def test_queue_manager_advance():
    """队列推进"""
    qm = QueueManager()
    item1 = QueueItem(name="A", blocks_dir=Path("/a/b"), workspace_dir=Path("/a"), status="pending")
    item2 = QueueItem(name="B", blocks_dir=Path("/b/b"), workspace_dir=Path("/b"), status="pending")
    qm.add_item(item1)
    qm.add_item(item2)

    current = qm.get_current()
    assert current is not None
    assert current.name == "A"

    current.status = "running"
    next_item = qm.advance()
    assert item1.status == "done"
    assert next_item is not None
    assert next_item.name == "B"
    print("✅ test_queue_manager_advance passed")


def test_queue_is_finished():
    """队列完成判断"""
    qm = QueueManager()
    assert qm.is_empty
    assert qm.is_finished  # empty is finished

    item = QueueItem(name="A", blocks_dir=Path("/a/b"), workspace_dir=Path("/a"), status="pending")
    qm.add_item(item)
    assert not qm.is_finished

    item.status = "done"
    assert qm.is_finished

    item.status = "failed"
    assert qm.is_finished

    item.status = "pending"
    assert not qm.is_finished
    print("✅ test_queue_is_finished passed")


def test_queue_persistence():
    """队列持久化（to_dict → from_dict）"""
    qm = QueueManager()
    items = [
        QueueItem(name="A", blocks_dir=Path("/a/b"), workspace_dir=Path("/a"), status="done", total_chapters=50, completed_chapters=50),
        QueueItem(name="B", blocks_dir=Path("/b/b"), workspace_dir=Path("/b"), status="pending"),
        QueueItem(name="C", blocks_dir=Path("/c/b"), workspace_dir=Path("/c"), status="failed", error_message="test error"),
    ]
    for item in items:
        qm.add_item(item)

    # Serialize
    serialized = [item.to_dict() for item in qm.items]
    assert len(serialized) == 3
    assert serialized[0]["status"] == "done"
    assert serialized[2]["error_message"] == "test error"

    # Deserialize
    qm2 = QueueManager()
    for d in serialized:
        qm2.add_item(QueueItem.from_dict(d))

    assert qm2.count == 3
    assert qm2.items[0].name == "A"
    assert qm2.items[0].status == "done"
    assert qm2.items[2].error_message == "test error"
    print("✅ test_queue_persistence passed")


def test_record_chapter_stat_accumulates():
    """每章统计含重试成本字段，且总量累加"""
    from backend.services.queue_service import AnalysisService
    svc = object.__new__(AnalysisService)
    svc._chapter_stats = []
    svc._token_stats = {}
    svc._analysis_start_time = 0.0
    svc._analysis_end_time = None
    svc._runner_task = None
    svc._pipeline = None
    svc._stop_requested = False
    svc._total_retries = 0
    svc._total_failed_tokens = 0

    svc._record_chapter_stat({
        "chapter": 1, "elapsed": 1.0,
        "input_tokens": 10, "output_tokens": 20,
        "retries": 1, "failed_tokens": 30,
    })
    svc._record_chapter_stat({
        "chapter": 2, "elapsed": 2.0,
        "input_tokens": 5, "output_tokens": 5,
        "retries": 0, "failed_tokens": 0,
    })

    stats = svc.token_stats()
    assert stats["total_retries"] == 1
    assert stats["total_failed_tokens"] == 30
    assert stats["chapter_stats"][0]["retries"] == 1
    assert stats["chapter_stats"][0]["failed_tokens"] == 30
    assert stats["chapter_stats"][0]["input_tokens"] == 10


def test_record_chapter_stat_failed_payload():
    """失败块的 retries/failed_tokens 同样计入总量与每章记录（失败成本不可归零）"""
    from backend.services.queue_service import AnalysisService
    svc = object.__new__(AnalysisService)
    svc._chapter_stats = []
    svc._token_stats = {}
    svc._analysis_start_time = 0.0
    svc._analysis_end_time = None
    svc._runner_task = None
    svc._pipeline = None
    svc._stop_requested = False
    svc._total_retries = 0
    svc._total_failed_tokens = 0

    # 失败 payload 只有 chapter/status/retries/failed_tokens（无 elapsed/input/output）
    svc._record_chapter_stat({
        "chapter": 3, "status": "failed",
        "retries": 4, "failed_tokens": 200,
    })

    stats = svc.token_stats()
    assert stats["total_retries"] == 4
    assert stats["total_failed_tokens"] == 200
    assert stats["chapter_stats"][0]["chapter"] == 3
    assert stats["chapter_stats"][0]["status"] == "failed"
    assert stats["chapter_stats"][0]["retries"] == 4
    assert stats["chapter_stats"][0]["failed_tokens"] == 200
    # 缺失的耗时/token 字段安全降级为 0
    assert stats["chapter_stats"][0]["elapsed"] == 0
    assert stats["chapter_stats"][0]["input_tokens"] == 0
    assert stats["chapter_stats"][0]["output_tokens"] == 0


def test_token_stats_elapsed_frozen_after_run():
    """运行结束后 elapsed 冻结为结束时刻与开始时刻之差，不再随当前时间增长"""
    from backend.services.queue_service import AnalysisService
    svc = object.__new__(AnalysisService)
    svc._chapter_stats = []
    svc._token_stats = {}
    svc._analysis_start_time = 1000.0
    svc._analysis_end_time = 1005.0
    svc._runner_task = None
    svc._pipeline = None
    svc._stop_requested = False
    svc._total_retries = 0
    svc._total_failed_tokens = 0

    stats = svc.token_stats()
    assert stats["elapsed"] == 5.0


# ============ H17 (2026-08-26) 发现流提取 + block_start 转发 单测 ============

def test_extract_discovery_normal():
    """正常提取：events 数 / foreshadows 截 24 字 / characters 首次登场 / unresolved 截 24 字"""
    result = {
        "core_events": [
            {"characters": "林动,萧炎", "summary": "x"},
            {"characters": "众人,林动", "summary": "y"},  # 众人=停用词；林动已见
        ],
        "foreshadowing": [
            {"clue": "古碑现世" + "X" * 30},  # 30+ 字符，截 24
        ],
        "cross_block": {
            "unresolved_questions": ["谁是幕后黑手？" * 5],  # 截 24 字
        },
    }
    seen = set()
    disc = _extract_discovery(result, seen)
    assert disc is not None
    assert disc["events"] == 2
    assert len(disc["foreshadows"]) == 1
    assert len(disc["foreshadows"][0]) == 24
    assert disc["characters"] == ["林动", "萧炎"]  # 首次登场（顺序：林动 在 event1，萧炎 在 event1）
    assert "林动" in seen
    assert "萧炎" in seen
    assert len(disc["unresolved"]) == 1
    assert len(disc["unresolved"][0]) == 24


def test_extract_discovery_stop_words():
    """停用词过滤：众人/他们/旁白 等不入"新人物"列表"""
    result = {
        "core_events": [
            {"characters": "众人,他们,旁白,自己,他,她,它"},
        ],
        "foreshadowing": [],
        "cross_block": {"unresolved_questions": []},
    }
    seen = set()
    disc = _extract_discovery(result, seen)
    assert disc["characters"] == []
    assert seen == set()  # 停用词不污染 seen 集合
    # 停用词表本身检查
    assert "众人" in _DISCOVERY_STOP_WORDS
    assert "旁白" in _DISCOVERY_STOP_WORDS
    assert "自己" in _DISCOVERY_STOP_WORDS


def test_extract_discovery_character_dedup():
    """人物去重：跨块/同块多次出现的同一人物只算一次"""
    result1 = {"core_events": [{"characters": "林动,萧炎"}], "foreshadowing": [], "cross_block": {"unresolved_questions": []}}
    result2 = {"core_events": [{"characters": "林动,林动,萧炎,药老"}], "foreshadowing": [], "cross_block": {"unresolved_questions": []}}
    seen = set()
    disc1 = _extract_discovery(result1, seen)
    disc2 = _extract_discovery(result2, seen)
    assert disc1["characters"] == ["林动", "萧炎"]
    assert disc2["characters"] == ["药老"]  # 林动/萧炎 已见，只新加药老


def test_extract_discovery_empty_or_invalid():
    """空 / 非 dict 输入安全降级"""
    assert _extract_discovery(None, set()) is None
    assert _extract_discovery({}, set()) is not None  # 空 dict 返回空 dict
    assert _extract_discovery({"core_events": None}, set()) is not None
    # 长度过滤：单字/超长不入
    result = {
        "core_events": [{"characters": "甲,abcdefghij"}],  # 1 字 + 10 字（>8）都过滤
        "foreshadowing": [],
        "cross_block": {"unresolved_questions": []},
    }
    disc = _extract_discovery(result, set())
    assert disc["characters"] == []
    # 顿号分隔也支持
    result2 = {"core_events": [{"characters": "林动、萧炎"}], "foreshadowing": [], "cross_block": {"unresolved_questions": []}}
    disc2 = _extract_discovery(result2, set())
    assert "林动" in disc2["characters"]
    assert "萧炎" in disc2["characters"]


async def test_on_progress_block_start_publishes():
    """on_progress 收到 status=start → 发布 block_start 消息"""
    from backend.progress_hub import get_hub
    hub = get_hub()
    # 用真实 hub（开发期 singleton），订阅一个临时队列
    test_queue = await hub.subscribe()
    try:
        # 用 object.__new__ 构造 AnalysisService 避免 __init__ 副作用
        svc = object.__new__(AnalysisService)
        svc._seen_characters = set()
        # 直接重写 _record_chapter_stat 避免依赖 _chapter_stats
        svc._record_chapter_stat = lambda payload: None

        # 复刻 on_progress 闭包（不直接调 _run_one_item，那是 200 行嵌套）
        async def on_progress_like(payload):
            status = payload.get("status", "")
            if status == "start":
                await hub.publish({
                    "type": "block_start",
                    "payload": {
                        "chapter": payload.get("chapter", 0),
                        "range": payload.get("message", ""),
                        "progress": payload.get("progress", 0),
                        "total": payload.get("total", 0),
                    },
                })

        await on_progress_like({
            "status": "start",
            "chapter": 5,
            "message": "开始分析第5-8章...",
            "progress": 5,
            "total": 100,
        })

        # 验证收到 block_start
        import json
        msg_raw = await asyncio.wait_for(test_queue.get(), timeout=2.0)
        msg = msg_raw if isinstance(msg_raw, dict) else json.loads(msg_raw)
        assert msg["type"] == "block_start"
        assert msg["payload"]["chapter"] == 5
        assert "5-8" in msg["payload"]["range"]
        assert msg["payload"]["progress"] == 5
    finally:
        await hub.unsubscribe(test_queue)


if __name__ == "__main__":
    test_record_chapter_stat_accumulates()
    test_record_chapter_stat_failed_payload()
    test_token_stats_elapsed_frozen_after_run()
    test_queue_item_roundtrip()
    test_queue_manager_add_remove()
    test_queue_manager_dedup()
    test_queue_manager_move()
    test_queue_manager_advance()
    test_queue_is_finished()
    test_queue_persistence()
    test_extract_discovery_normal()
    test_extract_discovery_stop_words()
    test_extract_discovery_character_dedup()
    test_extract_discovery_empty_or_invalid()
    test_on_progress_block_start_publishes()
    print("\n🎉 All queue tests passed!")
