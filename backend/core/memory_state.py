"""
内存状态容器（asyncio 版）
持有所有 AnalysisResult 和增量维护的 KnowledgeBase，避免重复磁盘IO
（自旧项目 core/memory_state.py 移植：threading.Lock → asyncio.Lock，
 涉锁方法改为 async；flush/restore 逻辑与磁盘格式零改动，block_size 续跑校验保留）
"""

import asyncio
import json
import logging
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set

from ..models.analysis_result import AnalysisResult
from ..models.knowledge import KnowledgeBase
from ..config.constants import (
    MAX_COMPRESSED_ARCS, MAX_RECENT_SUMMARIES,
    MAX_FORESIGHT_NETWORK, MAX_WORLD_BUILDING, MAX_VERIFIED_FACTS,
    MAX_LONG_TERM_ARCS, MAX_THEMATIC_ELEMENTS, ROLLING_FILE_NAME,
)
from ..utils.json_utils import safe_save_json, safe_load_json

logger = logging.getLogger(__name__)


class MemoryState:
    """内存状态容器，持有所有 AnalysisResult 并增量维护 KnowledgeBase"""

    def __init__(self, checkpoint_interval: int = 5, kb_limits: Optional[dict] = None):
        self.results: Dict[int, AnalysisResult] = {}
        self.kb: KnowledgeBase = KnowledgeBase()
        self.rolling: dict = {}
        self._failed_chapters: Dict[int, str] = {}
        self._flushed_chapters: Set[int] = set()
        self._checkpoint_interval: int = checkpoint_interval
        self._kb_lock = asyncio.Lock()
        self._rolling_lock = asyncio.Lock()  # 保护 state.rolling 的并发读写
        self._seen_facts: Set[str] = set()
        self._seen_world: Set[str] = set()
        self._seen_themes: Set[str] = set()
        self._seen_arcs: Set[str] = set()
        # get_kb_snapshot 子集缓存（仅缓存"已构建的最大 limit"，保证不含未来章节）
        self._snapshot_cache_limit: Optional[int] = None
        self._snapshot_cache_kb: Optional[KnowledgeBase] = None
        self._snapshot_lock = threading.Lock()  # 快照缓存并发保护（事件循环内短临界区）
        # 知识库限制（来自配置，默认回退到常量）
        self._kb_limits = kb_limits or {}

    async def add_result(self, result: AnalysisResult) -> None:
        """存储结果并增量更新 KB（协程安全）"""
        self.results[result.chapter_number] = result
        # 成功落盘的结果清除该章失败标记：重跑成功的章节不应再被永久报告为失败
        self._failed_chapters.pop(result.chapter_number, None)
        async with self._kb_lock:
            self._merge_one(result)
            # 子集缓存失效策略（2026-08-07 优化）：仅当新结果的章号落在已缓存
            # limit 区间内才失效（该结果无法被"按章号区间增量扩展"覆盖）；
            # 区间外的新结果（正常顺序分析）可直接增量扩展，避免每块全量重建 KB。
            if (self._snapshot_cache_limit is not None
                    and result.chapter_number <= self._snapshot_cache_limit):
                self._snapshot_cache_limit = None

    def add_failed(self, chapter_number: int, error: str) -> None:
        """记录失败章节"""
        self._failed_chapters[chapter_number] = error

    def get_kb_snapshot(self, chapter_limit: Optional[int] = None) -> KnowledgeBase:
        """
        获取 KB 快照（调用方拥有独立副本）。
        chapter_limit 为 None 或 >= 最大章号时返回当前 KB 深拷贝，
        否则返回"仅包含章号 <= chapter_limit 结果"的子集 KB。

        正确性约束（2026-08-07 修复）：子集缓存只允许在请求 limit 等于或大于
        缓存 limit 时复用/扩展；绝不能用覆盖更大章号的缓存回答更小的 limit，
        否则并发分析中低章号块会"读到未来章节知识"（原实现 bug，P0-1）。
        """
        # 快照 keys 避免并发修改
        keys = list(self.results.keys())
        if chapter_limit is None or not keys or chapter_limit >= max(keys):
            return KnowledgeBase.from_dict(self.kb.to_dict())

        with self._snapshot_lock:
            cached_limit = self._snapshot_cache_limit

            # 命中：请求与缓存 limit 精确相等
            if cached_limit is not None and chapter_limit == cached_limit:
                return KnowledgeBase.from_dict(self._snapshot_cache_kb.to_dict())

            # 命中：请求 limit 大于缓存 limit → 增量扩展（O(新增章节数)，非全量重建）
            if cached_limit is not None and chapter_limit > cached_limit:
                new_results = sorted(
                    (r for ch, r in self.results.items() if cached_limit < ch <= chapter_limit),
                    key=lambda r: r.chapter_number,
                )
                kb = KnowledgeBase.from_dict(self._snapshot_cache_kb.to_dict())
                if new_results:
                    self._extend_kb(kb, new_results)
                self._snapshot_cache_limit = chapter_limit
                self._snapshot_cache_kb = kb
                return KnowledgeBase.from_dict(kb.to_dict())

            # 未命中：请求 limit 小于缓存 limit（或首次构建）→ 从子集重建，
            # 保证不包含任何 > chapter_limit 的未来章节
            subset = sorted(
                (r for ch, r in self.results.items() if ch <= chapter_limit),
                key=lambda r: r.chapter_number,
            )
            kb = self._build_kb_from_results(subset)
            if cached_limit is None or chapter_limit > cached_limit:
                self._snapshot_cache_limit = chapter_limit
                self._snapshot_cache_kb = kb
            return KnowledgeBase.from_dict(kb.to_dict())

    async def flush_to_disk(self, output_dir: Path) -> int:
        """
        将未落盘的结果写入 output_dir。
        返回本次新落盘的章节数。
        （磁盘格式与旧项目完全一致：chapter_N_result.json / rolling_summary.json / chapter_N_failed.json）
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        newly_flushed = 0

        # 快照 self.results 避免迭代期间并发修改导致 RuntimeError
        snapshot = list(self.results.items())

        for ch, result in snapshot:
            if ch in self._flushed_chapters:
                continue
            data = result.to_dict()
            data.pop("raw_response", None)
            filepath = output_dir / f"chapter_{ch}_result.json"
            try:
                await asyncio.to_thread(self._write_json_file, filepath, data)
                self._flushed_chapters.add(ch)
                newly_flushed += 1
                # 成功落盘后移除该章陈旧的失败标记文件，避免已成功章节仍被报告为失败
                failed_path = output_dir / f"chapter_{ch}_failed.json"
                if failed_path.exists():
                    try:
                        await asyncio.to_thread(failed_path.unlink)
                    except OSError:
                        pass
            except Exception as e:
                logger.warning(f"落盘章节 {ch} 失败: {e}")

        # 写入滚动摘要
        if self.rolling:
            async with self._rolling_lock:
                rolling_snapshot = dict(self.rolling)
            rolling_path = output_dir / ROLLING_FILE_NAME
            await asyncio.to_thread(safe_save_json, rolling_snapshot, rolling_path)

        # 写入失败章节标记
        for ch, error in self._failed_chapters.items():
            failed_path = output_dir / f"chapter_{ch}_failed.json"
            try:
                await asyncio.to_thread(
                    safe_save_json, {"chapter_number": ch, "error": error}, failed_path
                )
            except Exception as e:
                logger.warning(f"落盘失败标记 {ch} 失败: {e}")

        return newly_flushed

    @staticmethod
    def _write_json_file(filepath: Path, data: dict) -> None:
        """同步写单个 JSON 文件（供 to_thread 调用，格式与旧项目一致）"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    async def restore_from_disk(self, output_dir: Path, block_size: Optional[int] = None) -> int:
        """
        从 output_dir 恢复结果到内存。
        返回恢复的章节数。
        """
        import glob as glob_mod
        pattern = str(output_dir / "chapter_*_result.json")
        files = sorted(glob_mod.glob(pattern))

        restored = 0
        for filepath_str in files:
            filepath = Path(filepath_str)
            if block_size is not None and not self._validate_block_size(filepath, block_size):
                continue

            m = re.match(r'chapter_(\d+)_result\.json', filepath.name)
            if not m:
                continue
            ch_num = int(m.group(1))

            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                result = AnalysisResult.from_dict(data)
                self.results[ch_num] = result
                self._flushed_chapters.add(ch_num)
                restored += 1
            except Exception as e:
                logger.warning(f"恢复 {filepath.name} 失败: {e}")

        # 恢复后重建 KB
        if restored > 0:
            sorted_results = [self.results[ch] for ch in sorted(self.results.keys())]
            async with self._kb_lock:
                self.kb = KnowledgeBase()
                self._seen_facts.clear()
                self._seen_world.clear()
                self._seen_themes.clear()
                self._seen_arcs.clear()
                for r in sorted_results:
                    self._merge_one(r)

        # 恢复滚动摘要
        rolling_path = output_dir / ROLLING_FILE_NAME
        if rolling_path.exists():
            try:
                rd = safe_load_json(rolling_path)
                if isinstance(rd, dict):
                    async with self._rolling_lock:
                        self.rolling = rd
            except Exception as e:
                logger.warning(f"恢复 rolling_summary 失败: {e}")

        # 恢复失败标记：已成功恢复结果的章节不应再被标记为失败（清除陈旧失败标记）
        restored_chapters = set(self.results.keys())
        failed_pattern = str(output_dir / "chapter_*_failed.json")
        for fp in sorted(glob_mod.glob(failed_pattern)):
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and "chapter_number" in data:
                    ch_num = data["chapter_number"]
                    if ch_num in restored_chapters:
                        continue  # 该章已有成功结果，跳过陈旧失败标记
                    self._failed_chapters[ch_num] = data.get("error", "")
            except Exception:
                pass

        return restored

    def should_checkpoint(self, batch_idx: int) -> bool:
        """判断是否应触发 checkpoint"""
        if self._checkpoint_interval <= 0:
            return False
        return (batch_idx + 1) % self._checkpoint_interval == 0

    def _merge_one(self, result: AnalysisResult) -> None:
        """增量合并一个结果到 self.kb（须在 _kb_lock 内调用）"""
        self._merge_result_into(result, self.kb, self._seen_facts, self._seen_world, self._seen_themes, self._seen_arcs)
        self._trim_kb(self.kb)

    @staticmethod
    def _merge_result_into(
        result: AnalysisResult,
        kb: KnowledgeBase,
        seen_facts: Set[str],
        seen_world: Set[str],
        seen_themes: Set[str],
        seen_arcs: Set[str],
    ) -> None:
        """将单个 result 合并到指定 kb（纯函数，无副作用）"""
        if result.cross_block.summary:
            kb.recent_summaries.append(result.cross_block.summary)

        timeline = result.updated_knowledge.timeline
        if timeline:
            if isinstance(timeline, list):
                timeline = ", ".join(str(t) for t in timeline)
            else:
                timeline = str(timeline)
            kb.compressed_arcs.append(timeline)

        for event in result.core_events:
            if event.event and event.event not in seen_facts:
                seen_facts.add(event.event)
                kb.verified_facts.append(event.event)

        for a in result.long_context_insights.long_term_arcs:
            if a not in seen_arcs:
                seen_arcs.add(a)
                kb.long_term_arcs.append(a)

        for w in result.updated_knowledge.world_building:
            if w not in seen_world:
                seen_world.add(w)
                kb.world_building.append(w)

        for arc in result.character_arcs:
            if arc.name and arc.surface_action:
                kb.character_states[arc.name] = arc.surface_action

        arcs_in_ch = [a for a in result.character_arcs if a.name]
        for i, a1 in enumerate(arcs_in_ch):
            for a2 in arcs_in_ch[i + 1:]:
                first, second = (a1, a2) if a1.name <= a2.name else (a2, a1)
                pair_key = f"{first.name}-{second.name}"
                desc = f"{first.change_delta}；{second.change_delta}"
                kb.character_relationships[pair_key] = desc

        for t in result.long_context_insights.thematic_elements:
            if t not in seen_themes:
                seen_themes.add(t)
                kb.thematic_elements.append(t)

        if result.long_context_insights.foreshadowing_network:
            kb.foreshadowing_network.append(
                f"第{result.chapter_number}章: {result.long_context_insights.foreshadowing_network}"
            )

        if timeline:
            kb.story_timeline = timeline

    def _trim_kb(self, kb: KnowledgeBase) -> None:
        """裁剪 KB 各字段到上限（优先使用配置值，回退到常量）"""
        L = self._kb_limits
        kb.recent_summaries = kb.recent_summaries[-L.get('max_recent_summaries', MAX_RECENT_SUMMARIES):]
        kb.compressed_arcs = kb.compressed_arcs[-L.get('max_compressed_arcs', MAX_COMPRESSED_ARCS):]
        kb.verified_facts = kb.verified_facts[-L.get('max_verified_facts', MAX_VERIFIED_FACTS):]
        kb.long_term_arcs = kb.long_term_arcs[-L.get('max_long_term_arcs', MAX_LONG_TERM_ARCS):]
        kb.world_building = kb.world_building[-L.get('max_world_building', MAX_WORLD_BUILDING):]
        kb.thematic_elements = kb.thematic_elements[-L.get('max_thematic_elements', MAX_THEMATIC_ELEMENTS):]
        kb.foreshadowing_network = kb.foreshadowing_network[-L.get('max_foreshadow_network', MAX_FORESIGHT_NETWORK):]

    def _extend_kb(self, kb: KnowledgeBase, new_results: List[AnalysisResult]) -> None:
        """将新结果增量合并进已有 KB（快照扩展用；须在 _snapshot_lock 内调用）。

        去重集合从 KB 现存字段重建（verified_facts 等本身就是去重后的列表），
        与 _merge_result_into 的语义保持一致。
        """
        seen_facts = set(kb.verified_facts)
        seen_world = set(kb.world_building)
        seen_themes = set(kb.thematic_elements)
        seen_arcs = set(kb.long_term_arcs)
        for r in new_results:
            self._merge_result_into(r, kb, seen_facts, seen_world, seen_themes, seen_arcs)
        self._trim_kb(kb)

    def _build_kb_from_results(self, results: List[AnalysisResult]) -> KnowledgeBase:
        """从结果列表构建全新 KB（用于 get_kb_snapshot 子集场景）"""
        kb = KnowledgeBase()
        seen_facts: Set[str] = set()
        seen_world: Set[str] = set()
        seen_themes: Set[str] = set()
        seen_arcs: Set[str] = set()

        results = sorted(results, key=lambda r: r.chapter_number)

        for r in results:
            self._merge_result_into(r, kb, seen_facts, seen_world, seen_themes, seen_arcs)

        self._trim_kb(kb)
        return kb

    @staticmethod
    def _validate_block_size(filepath: Path, expected_block_size: int) -> bool:
        """校验文件中的 block_size 是否匹配预期"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                head = f.read(500)
            m = re.search(r'"block_size"\s*:\s*(\d+)', head)
            if m is None:
                # 头部窗口未命中（新 schema 字段可能将 block_size 推后），回退完整解析
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, dict) or 'block_size' not in data:
                    return True  # 旧格式无此字段，视为兼容
                return int(data['block_size']) == expected_block_size
            return int(m.group(1)) == expected_block_size
        except Exception:
            return False
