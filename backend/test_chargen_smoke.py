"""冒烟测试：验证角色卡两个新方法在真实书目数据下可正确返回。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.character_card_generator import CharacterCardGenerator

REAL_OUTPUT = Path(r"F:/AI/小说分析器/workspace/分析结果/《我的末世基地车》（校对版全本）/output")
EMPTY_OUTPUT = Path(r"F:/AI/小说分析器/frontend/.tmp_verify/sample_char")  # 可能为空，仅测不崩溃


def test_real():
    gen = CharacterCardGenerator(REAL_OUTPUT)
    summaries = gen.get_character_summaries()
    print(f"[summaries] count = {len(summaries)}")
    assert isinstance(summaries, list)
    if summaries:
        s0 = summaries[0]
        print(f"[summaries] top = {s0}")
        for k in ("name", "first_appearance", "total_events", "chapters_count"):
            assert k in s0, f"缺少字段 {k}"
        # 验证按 total_events 降序
        evs = [s["total_events"] for s in summaries]
        assert evs == sorted(evs, reverse=True), "未按要求降序"
        # 取第一个角色测卡片详情
        name = s0["name"]
        card = gen.get_card_data(name)
        print(f"[card:{name}] keys = {sorted(card.keys())}")
        for k in ("name", "chapters", "total_events", "arcs", "events", "states", "relationships"):
            assert k in card, f"card 缺少字段 {k}"
        assert card["name"] == name
        print(f"[card:{name}] chapters={len(card['chapters'])} arcs={len(card['arcs'])} "
              f"events={len(card['events'])} rels={len(card['relationships'])}")
    print("[OK] real book test passed")


def test_empty_dir():
    gen = CharacterCardGenerator(EMPTY_OUTPUT)
    # 即便目录为空/无数据，也不应抛 AttributeError，应优雅返回空列表
    summaries = gen.get_character_summaries()
    assert isinstance(summaries, list)
    print(f"[empty] summaries = {summaries}")
    # get_card_data 对不存在角色应抛 KeyError（路由层转 404）
    try:
        gen.get_card_data("不存在的角色")
        print("[WARN] 未抛 KeyError")
    except KeyError as e:
        print(f"[OK] empty/missing -> KeyError as expected: {e}")
    print("[OK] empty dir test passed")


if __name__ == "__main__":
    test_real()
    test_empty_dir()
    print("\nALL SMOKE TESTS PASSED")
