"""
伏笔账本模块
管理伏笔的生命周期：新增、回收、休眠判定
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime

from ..utils.json_utils import safe_save_json, safe_load_json

logger = logging.getLogger(__name__)

# Schema 版本号
# v1: 初始版本
# v2: ForeshadowItem 新增 needs_review 可选字段（向后兼容，旧 v1 JSON 仍可加载）
SCHEMA_VERSION = 2

# 休眠阈值
DORMANT_BATCH_THRESHOLD = 15      # 批次不活跃阈值
DORMANT_CHAPTER_THRESHOLD = 200   # 章节跨度阈值

# 置信度字符串→数值映射（LLM输出"高"/"中"/"低"时使用）
_CONFIDENCE_MAP = {"高": 0.9, "中": 0.6, "低": 0.3}


@dataclass
class ForeshadowItem:
    """单个伏笔条目"""
    id: str                                # 伏笔唯一ID
    description: str                       # 伏笔描述
    first_seen_chapter: int                # 首次出现章节
    first_seen_batch: int                  # 首次出现批次
    last_seen_chapter: int                 # 最后出现章节
    last_seen_batch: int                   # 最后出现批次
    status: str = "active"                 # active/resolved/dormant
    resolution: Optional[str] = None       # 回收方式描述
    resolved_chapter: Optional[int] = None # 回收章节
    resolved_batch: Optional[int] = None   # 回收批次
    source_type: str = "llm_judged"        # llm_judged/rule_detected/human_override/auto_detected
    evidence_chapters: List[int] = field(default_factory=list)  # 证据章节列表
    confidence: float = 0.8                # 置信度 0-1
    notes: List[str] = field(default_factory=list)  # 备注
    needs_review: bool = False             # 是否需要人工复核（低置信度 / 存疑时标记）

    @classmethod
    def from_dict(cls, data: dict) -> 'ForeshadowItem':
        """从字典创建（白名单过滤，防御 JSON 中的多余字段导致 TypeError）"""
        valid_keys = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class ForeshadowLedger:
    """伏笔账本"""
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    batch_size_snapshot: int = 0            # 当时的批次大小快照
    total_chapters: int = 0                 # 总章节数
    items: List[ForeshadowItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典"""
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'ForeshadowLedger':
        """从字典创建账本"""
        items = [ForeshadowItem.from_dict(item) for item in data.get('items', [])]
        return cls(
            schema_version=data.get('schema_version', SCHEMA_VERSION),
            created_at=data.get('created_at', datetime.now().isoformat()),
            updated_at=data.get('updated_at', datetime.now().isoformat()),
            batch_size_snapshot=data.get('batch_size_snapshot', 0),
            total_chapters=data.get('total_chapters', 0),
            items=items
        )

    def save(self, path: Path) -> bool:
        """保存账本到文件"""
        try:
            self.updated_at = datetime.now().isoformat()
            return safe_save_json(self.to_dict(), path)
        except Exception as e:
            logger.error(f"保存伏笔账本失败: {e}")
            return False

    @classmethod
    def load(cls, path: Path) -> 'ForeshadowLedger':
        """从文件加载账本"""
        data = safe_load_json(path)
        if data is None:
            return cls()
        return cls.from_dict(data)

    def ingest_batch_evidence(
        self,
        batch_idx: int,
        chapter_start: int,
        chapter_end: int,
        evidence: Dict[str, Any]
    ) -> None:
        """
        从批次分析结果中提取伏笔信息并更新账本（兼容旧格式）
        """
        # 处理新伏笔
        for fs in evidence.get('new_foreshadows', []):
            fs_id = fs.get('id', '')
            if not fs_id:
                continue
            existing = self._find_item(fs_id)
            if existing:
                existing.last_seen_chapter = chapter_end
                existing.last_seen_batch = batch_idx
                existing.evidence_chapters = list(set(existing.evidence_chapters + fs.get('chapters', [])))
            else:
                _conf_raw = fs.get('confidence', 0.8)
                self.items.append(ForeshadowItem(
                    id=fs_id,
                    description=fs.get('desc', ''),
                    first_seen_chapter=chapter_start,
                    first_seen_batch=batch_idx,
                    last_seen_chapter=chapter_end,
                    last_seen_batch=batch_idx,
                    evidence_chapters=fs.get('chapters', []),
                    confidence=_CONFIDENCE_MAP.get(_conf_raw, 0.8) if isinstance(_conf_raw, str) else _conf_raw,
                    source_type='llm_judged'
                ))

        # 处理已回收伏笔
        for fs in evidence.get('resolved_foreshadows', []):
            fs_id = fs.get('id', '')
            if not fs_id:
                continue
            item = self._find_item(fs_id)
            if item:
                item.status = 'resolved'
                item.resolution = fs.get('resolution', '')
                item.resolved_chapter = fs.get('resolved_chapter', chapter_end)
                item.resolved_batch = batch_idx
                item.source_type = 'llm_judged'
                item.last_seen_chapter = chapter_end
                item.last_seen_batch = batch_idx

        # 处理活跃伏笔（更新 last_seen）
        for fs in evidence.get('active_foreshadows', []):
            fs_id = fs.get('id', '')
            if not fs_id:
                continue
            item = self._find_item(fs_id)
            if item:
                item.last_seen_chapter = chapter_end
                item.last_seen_batch = batch_idx
                item.evidence_chapters = list(set(item.evidence_chapters + fs.get('chapters', [])))

    def reconcile_and_update(self, batch_idx: int) -> None:
        """
        批次完成后执行 reconciliation：
        1. 检查休眠伏笔
        2. 更新状态
        """
        for item in self.items:
            if item.status != 'active':
                continue

            # 检查休眠条件：批次不活跃 AND 章节跨度
            batch_inactive = (batch_idx - item.last_seen_batch) >= DORMANT_BATCH_THRESHOLD
            chapter_span = (self.total_chapters - item.last_seen_chapter) >= DORMANT_CHAPTER_THRESHOLD

            if batch_inactive and chapter_span:
                item.status = 'dormant'
                item.notes.append(f"批次{batch_idx}时自动标记为休眠（{DORMANT_BATCH_THRESHOLD}批未见，{DORMANT_CHAPTER_THRESHOLD}章未见）")

    def summary_text_for_prompt(self) -> str:
        """
        生成供 LLM 使用的伏笔摘要文本
        """
        active = [i for i in self.items if i.status == 'active']
        dormant = [i for i in self.items if i.status == 'dormant']

        lines = []
        if active:
            lines.append("【活跃伏笔】")
            for item in active:
                chapters_str = ','.join(map(str, item.evidence_chapters[:5]))
                lines.append(f"- {item.id}: {item.description} (首次出现: 第{item.first_seen_chapter}章, 最近: 第{item.last_seen_chapter}章, 证据章节: [{chapters_str}])")

        if dormant:
            lines.append("\n【休眠伏笔（长期未出现）】")
            for item in dormant:
                lines.append(f"- {item.id}: {item.description} (首次出现: 第{item.first_seen_chapter}章, 最近: 第{item.last_seen_chapter}章)")

        return '\n'.join(lines) if lines else "（暂无伏笔记录）"

    def audit_text_for_report(self) -> str:
        """
        生成审计报告文本
        """
        active = [i for i in self.items if i.status == 'active']
        resolved = [i for i in self.items if i.status == 'resolved']
        dormant = [i for i in self.items if i.status == 'dormant']

        lines = ["## 伏笔审计报告\n"]
        lines.append(f"**总计**: {len(self.items)} 个伏笔")
        lines.append(f"- 活跃: {len(active)}")
        lines.append(f"- 已回收: {len(resolved)}")
        lines.append(f"- 休眠: {len(dormant)}\n")

        if resolved:
            lines.append("### 已回收伏笔")
            for item in resolved:
                lines.append(f"- **{item.id}**: {item.description}")
                lines.append(f"  - 回收方式: {item.resolution}")
                lines.append(f"  - 回收章节: 第{item.resolved_chapter}章")
                lines.append(f"  - 来源类型: {item.source_type}")
                if item.confidence < 0.8:
                    lines.append(f"  - 置信度: {item.confidence}（需人工复核）")

        if active:
            lines.append("\n### 活跃伏笔")
            for item in active:
                lines.append(f"- **{item.id}**: {item.description}")
                lines.append(f"  - 首次出现: 第{item.first_seen_chapter}章")
                lines.append(f"  - 最近出现: 第{item.last_seen_chapter}章")
                lines.append(f"  - 证据章节: {item.evidence_chapters}")

        if dormant:
            lines.append("\n### 休眠伏笔（长期未出现）")
            for item in dormant:
                lines.append(f"- **{item.id}**: {item.description}")
                lines.append(f"  - 首次出现: 第{item.first_seen_chapter}章")
                lines.append(f"  - 最近出现: 第{item.last_seen_chapter}章")
                lines.append(f"  - 休眠原因: {item.notes[-1] if item.notes else '未知'}")

        return '\n'.join(lines)

    def to_json(self) -> str:
        """导出为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def _find_item(self, fs_id: str) -> Optional[ForeshadowItem]:
        """查找伏笔条目"""
        for item in self.items:
            if item.id == fs_id:
                return item
        return None

    def get_item_count(self) -> Dict[str, int]:
        """获取各状态伏笔数量"""
        return {
            'total': len(self.items),
            'active': sum(1 for i in self.items if i.status == 'active'),
            'resolved': sum(1 for i in self.items if i.status == 'resolved'),
            'dormant': sum(1 for i in self.items if i.status == 'dormant')
        }


def validate_batch_json(data: dict) -> Tuple[bool, str]:
    """
    验证批次分析结果的JSON结构（兼容新旧格式）

    Returns:
        (是否有效, 错误信息)
    """
    # 新格式：foreshadow_reconciliation
    if 'foreshadow_reconciliation' in data:
        reconciliation = data['foreshadow_reconciliation']
        if not isinstance(reconciliation, list):
            return False, "foreshadow_reconciliation 必须为列表类型"
        return True, ""
    
    # 旧格式：foreshadow_evidence
    if 'foreshadow_evidence' in data:
        evidence = data['foreshadow_evidence']
        if not isinstance(evidence, dict):
            return False, "foreshadow_evidence 必须为字典类型"
        for key in ['new_foreshadows', 'resolved_foreshadows', 'active_foreshadows']:
            if key not in evidence:
                return False, f"foreshadow_evidence 缺少字段: {key}"
            if not isinstance(evidence.get(key), list):
                return False, f"foreshadow_evidence.{key} 必须为列表类型"
        return True, ""
    
    return False, "缺少 foreshadow_reconciliation 或 foreshadow_evidence 字段"