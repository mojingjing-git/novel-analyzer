"""
测试 QueueItem 和 QueueManager 的增删改/持久化/恢复
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.services.queue_service import QueueItem, QueueManager


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


if __name__ == "__main__":
    test_queue_item_roundtrip()
    test_queue_manager_add_remove()
    test_queue_manager_dedup()
    test_queue_manager_move()
    test_queue_manager_advance()
    test_queue_is_finished()
    test_queue_persistence()
    print("\n🎉 All queue tests passed!")
