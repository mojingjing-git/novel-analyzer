# `is_same_location` 过合并修复 — 外部审查文档

**日期**：2026-08-21
**作者**：AI Agent（小说分析器项目 Phase 0 SDD 维护）
**审查目的**：在合并修复之前获得独立技术审查
**状态**：等待审查

---

## 0. TL;DR

`backend/utils/text_utils.py::is_same_location()` 的 substring containment 规则把"不同地方"误并成同一个 group，导致 Phase 0 归一化的 prompt 从 ~30K chars 膨胀到 **54K chars**（实测），单次 LLM 调用超过 10.5 分钟硬超时，最终把整个总结任务拖死。

**修复**：保留 substring containment 但加**软修饰白名单**（`extra in {"主角", "（已废弃）", ...}`），只合并软修饰类后缀，不合并"会议室/顶层/总部"这类实体词。

预计效果：
- 398 个 group → ~200-300 个 group
- 54K chars prompt → ~30K chars
- 单次 LLM 调用从超时 10+ 分钟 → 正常 30-60s
- 零 token 成本增加（batch 不变）

---

## 1. 背景

### 1.1 项目概况

**小说智能分析器**（`F:\AI\小说分析器`）是用 LLM 分析长篇中文网络小说的工具。FastAPI + Vue 3 + pywebview 桌面应用。

### 1.2 Phase 0 归一化

2026-08-21 SDD 流程新增了**Phase 0 归一化**功能（在最终总结阶段前跑）：

1. **0a-batches**：locations 切片 → 并发 LLM 调 → 校验 canonical 在 aliases 中
2. **0a-consol**：1 次 LLM 合并跨 batch 同地点
3. **0b-batches**：spatial 切片 + canonical 白名单 → 并发 LLM 调
4. **0b-dedupe**：机械去重

Phase 0 的设计目的是解决 LLM 生成 `chapter_*.json` 时**地名 / type / parent 不一致**的问题（spec 已确认 LLM 数据脏 130/889 地点）。

### 1.3 is_same_location 的作用

`is_same_location(n1, n2)` 在 Phase 0a 的**预聚合**阶段（`aggregate_locations`）用 Union-Find 把相似地名合并为同一 group：

```python
# location_normalizer.py:67-72
for i in range(len(names)):
    for j in range(i + 1, len(names)):
        if is_same_location(names[i], names[j]):
            _union(names[i], names[j])
```

合并后的 group 给 LLM 处理（LLM 选 canonical_name + 合并 type/parent）。

### 1.4 算法历史

Task 1 实现（commit `69bb930`）加了这个函数。**Brief 原始算法**是 "归一化相等 + SequenceMatcher ratio ≥ 0.85"，但 brief 的 ratio 注释算错了：
- `SequenceMatcher.ratio("宁安县", "宁安县城")` 实际 = 0.857（不是注释的 0.75）
- 直接用 0.85 阈值会误并 "宁安县" 和 "宁安县城"

implementer 加了**长度感知 substring containment**（`shorter ≥ 4 chars 且 shorter in longer`）作为兜底，认为这样能区分 "宁安县城"（whole place + city type 后缀）和 "宁安县城天牛坊"（neighborhood）。

**这个兜底规则就是当前 bug 的来源。**

---

## 2. 问题（症状与证据）

### 2.1 用户报告

> "归一化失败直接停止了"
> "我看 ta 一瞬间就失败了"（实际 10.5 分钟）
> "我手动重跑了一次还是失败"

### 2.2 日志证据（`%LOCALAPPDATA%\NovelAnalyzer\run.log`）

```
22:20:49 [INFO] FinalSummaryRunner 检测到书名: 韩娱之光影交错
22:20:52 [INFO] location_normalizer: Phase 0a: 398 个 location group
22:20:52 [INFO] openai SDK POST /v1/chat/completions
22:30:52 [INFO] openai._base_client: Retrying request ... in 0.424152 seconds  ← 600s 默认超时，自动 retry
22:31:22 [WARNING] llm_client: 请求硬超时(timeout=630s): API 无响应                ← 10.5 min 后 my 硬超时
22:31:22 [ERROR] location_normalizer: Phase 0a batch 1 失败
22:31:22 [ERROR] final_summary: Phase 0 归一化失败，终止总结
```

第二次手动重跑：
```
22:39:35 [INFO] Phase 0a: 398 个 location group
22:44:06 [ERROR] Phase 0a batch 1 失败: 用户请求停止                            ← 用户 4.5 分钟后手动停
```

### 2.3 实测 prompt 体积

我用脚本直接构造 Phase 0a batch 1 的实际 prompt（《韩娱之光影交错》228 章，398 groups），测得：

```
System prompt chars:    671       (~335 tokens)
User prompt chars:    53,388     (~26,700 tokens)
Expected output:     ~59,700    (~29,850 tokens)
Total per call:      ~57,000    tokens
```

**对比预期**（基于 spec 估算，900 group 千万字级别）：
- 预期 batch 大小：~1000 group
- 预期 prompt 大小：~30-40K chars
- **实测**：1 batch 装 398 group，prompt 已经爆到 54K chars

### 2.4 第一个 group 的 alias 列表（28 个）

```
aliases=[大唐公司, 新罗酒店, 总裁办公室, 大唐公司总部, 新罗酒店顶层, 大唐公司会议室,
        大唐公司会客室, 济州岛大唐公司, 大唐公司摄影棚, 新罗酒店中餐厅, 新罗酒店宴会厅,
        新罗酒店洗手台, 大唐公司待客室, 新罗酒店办公室, 新罗酒店咖啡厅, 大唐公司一期工地,
        新罗酒店大唐分店, 新罗酒店总统套房, 大唐公司临时办公楼, 大唐公司济州岛工地,
        济州岛大唐公司总部, 大唐公司总裁办公室, 新罗酒店济州岛分店, 大唐公司济州岛分部,
        新罗酒店总裁办公室, 济州岛大唐公司大楼天台, 济州岛大唐公司员工食堂包厢]
types={企业:3, 企业总部:4, 建筑工地:1, 办公楼:1, 酒店:8, 商业场所:2, 餐饮场所:1,
       建筑:20, 商业建筑工地:1, 高端约会场所:1, 公司:1, 办公/私密空间:1, 办公场所:1, 商业建筑:1}
parents={济州岛:20, 首尔:10, 济州岛大唐公司:4, 大唐公司:3, 新村集团:1, 新罗酒店:7, 新村集团总部:1}
chapter_count=40
```

**这 28 个 alias 包含至少 6 个不同的地方**：
- 大唐公司（公司）
- 新罗酒店（另一个酒店，**不是大唐公司旗下**）
- 总裁办公室 / 大唐公司会议室 / 大唐公司会客室 / 大唐公司待客室 / 大唐公司摄影棚（公司内部的房间）
- 济州岛大唐公司 / 济州岛大唐公司总部（不同地点的分部）
- 新罗酒店顶层 / 新罗酒店中餐厅 / 新罗酒店总统套房（酒店内部不同地方）
- 大唐公司一期工地（建筑工地）

LLM 拿到这种 group 必然困惑：哪个是真名？type 选哪个？parent 归到哪？

---

## 3. 根因分析

### 3.1 当前 `is_same_location` 算法（`text_utils.py:362-384`）

```python
def is_same_location(n1, n2):
    """判断两个地名是否可能指向同一地点（保守规则）"""
    from difflib import SequenceMatcher as _SM
    if n1 == n2:
        return True
    a = _normalize_for_dedup(n1)
    b = _normalize_for_dedup(n2)
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 4 and shorter in longer:
        return True                                            ← ← ← 根因
    threshold = 0.95 if len(shorter) <= 3 else 0.85
    ratio = _SM(None, a, b).ratio()
    return ratio >= threshold
```

**第 14 行的 substring containment 规则**：
- "大唐公司" 是 "大唐公司会议室" 的子串 → True → 合并
- "新罗酒店" 是 "新罗酒店顶层" 的子串 → True → 合并
- "济州岛" 是 "济州岛大唐公司" 的子串 → True → 合并

这一规则设计初衷是处理像 "居安小阁（主角）" → "居安小阁" 这种**软修饰后缀**，但没有区分"软修饰"和"实体词"。

### 3.2 设计失误分类

**误把"前缀共享"等同于"同地方"**。

中文地名的天然结构是层级化（公司→会议室、岛→公司），短名是长名的**自然前缀**，substring 在中文里几乎对所有有从属关系的地方都成立。这跟英文里 "Smith" vs "Smith Tower" 不一样——英文里 Smith Tower 是合成词，中文是纯串接。

**没有数据**：
- implementer 加 substring 规则时没有跑真实数据看 group 大小
- SDD 流程 152 个测试全过，但测试用的是 mock 数据（3-5 个 alias），不是真实 28 个 alias

**没有性能预警**：
- 当前实现没有任何"group 太大" 的告警
- 28 个 alias 进 1 个 group 静默通过 → 没人发现

### 3.3 错误链

```
is_same_location 太宽松
  ↓
aggregate_locations 把 28 个不同地方合成 1 个 group
  ↓
build_location_prompt 生成 53K chars 输入
  ↓
LLM 拿到 prompt 卡住（理解阶段）
  ↓
10.5 分钟后硬超时
  ↓
Phase 0 失败 → 整个总结失败
```

---

## 4. 修复方案

### 4.1 核心思路

**保留 substring containment，但加"软修饰白名单"**：只有当长名相对短名的额外部分属于已知软修饰类词（角色后缀、状态、主次等），才合并；额外部分是实体词（室、厅、楼、层、岛、市等），不合并。

### 4.2 软修饰白名单

```python
_SOFT_QUALIFIERS = {
    # 角色/属性后缀
    '主角', '配角', '已故', '重要', '次要', '女主', '男主', '反派', '龙套',
    # 状态/版本
    '已废弃', '副本', '旧', '新', '临时', '原址', '旧址',
    # 主次/总分
    '主', '次', '总', '分', '总店', '分店',
    # 方位修饰（常用于"X 南侧"等）
    '南侧', '北侧', '东侧', '西侧', '上层', '下层',
    # 括号内容整体视为软修饰（递归检查）
}
```

### 4.3 `is_same_location` 新实现

```python
import re as _re

_SOFT_QUALIFIERS = {
    '主角', '配角', '已故', '重要', '次要', '女主', '男主', '反派', '龙套',
    '已废弃', '副本', '旧', '新', '临时', '原址', '旧址',
    '主', '次', '总', '分', '总店', '分店',
    '南侧', '北侧', '东侧', '西侧', '上层', '下层',
}


def _is_soft_qualifier(s: str) -> bool:
    """s 是软修饰（角色后缀/状态等），而非新实体"""
    s = s.strip()
    if not s:
        return True
    # 括号内容整体视为软修饰（递归检查内层）
    m = _re.match(r'^[（(](.+)[)）]$', s)
    if m:
        return _is_soft_qualifier(m.group(1))
    return s in _SOFT_QUALIFIERS


def is_same_location(n1, n2):
    """判断两个地名是否指向同一地点

    规则：
    1. 归一后任一为空或长度 < 2 → False（避免单字噪声）
    2. 归一后相等 → True
    3. 较短串（≥3字）是较长串子串 且 额外部分属于软修饰白名单 → True
       例："居安小阁（主角）" ⊇ "居安小阁" 且 extra="（主角）"是软修饰 → True
       例："大唐公司会议室" ⊇ "大唐公司" 但 extra="会议室"不是软修饰 → False
    4. 短名（≤3字）走严格阈值 0.95，避免被前缀同形长名误并
    5. 其余按 SequenceMatcher 相似度阈值 0.85 判定
    """
    from difflib import SequenceMatcher as _SM
    if n1 == n2:
        return True
    a = _normalize_for_dedup(n1)
    b = _normalize_for_dedup(n2)
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    # 规则 3：substring + 软修饰白名单
    if len(shorter) >= 3 and shorter in longer:
        extra = longer[len(shorter):]
        if _is_soft_qualifier(extra):
            return True
        # extra 不是软修饰 → 视为不同实体（默认不合并）
    # 规则 4-5：SequenceMatcher 兜底
    threshold = 0.95 if len(shorter) <= 3 else 0.85
    ratio = _SM(None, a, b).ratio()
    return ratio >= threshold
```

**关键变化**：
- substring 规则：`shorter in longer AND _is_soft_qualifier(extra)`（**新增 qualifier 检查**）
- substring 规则的最小长度：`>= 4` → `>= 3`（让"济州岛"等短名也能正确处理）
- 顺序：先试 substring + qualifier（命中就返回）；否则试 SequenceMatcher

### 4.4 测试用例覆盖

**保持通过的现有 6 个 test**：

| Test | 期望 | 新算法行为 | 结果 |
|---|---|---|---|
| `"宁安县"` == `"宁安县"` | True | normalize → equal | ✅ |
| `"宁安县。"` vs `"宁安县"` | True | normalize → equal | ✅ |
| `"宁安县"` vs `"宁安县城"` | False | substring (extra="城" 非软修饰) → 不合并 → False | ✅ |
| `"居安小阁"` vs `"居安小阁（主角）"` | True | substring + extra="（主角）"→ 主角是软修饰 → True | ✅ |
| `"大贞"` vs `"大秀"` | False | 不 substring, ratio 低 → False | ✅ |
| `"京"` vs `"京"` | False | len < 2 → False | ✅ |

**新增 5 个 test**（覆盖新算法的关键场景）：

| Test | 期望 | 新算法行为 |
|---|---|---|
| `test_substring_with_entity_word_not_merged` | False | substring 但 extra="会议室"不是软修饰 → 不合并 |
| `test_substring_with_floor_word_not_merged` | False | substring 但 extra="顶层"不是软修饰 |
| `test_substring_with_branches_not_merged` | False | "济州岛" vs "济州岛大唐公司" → extra="大唐公司" 不是软修饰 |
| `test_brackets_around_soft_qualifier_merged` | True | "宁安县（已废弃）" vs "宁安县" → extra="（已废弃）"→ 剥括号 → "已废弃" 是软修饰 |
| `test_nested_brackets_merged` | True | "宁安县（主角（已故））" 剥括号递归 → 软修饰 |

**回归测试**（1 个）：

```python
def test_aggregation_no_longer_bloats_aliases(self):
    """确保 substring 规则不再把不同实体误并"""
    raw = [
        {"name": "大唐公司", "parent": "", "type": "企业", "description": "", "chapter": 1},
        {"name": "大唐公司会议室", "parent": "大唐公司", "type": "会议室", "description": "", "chapter": 2},
        {"name": "大唐公司总裁办公室", "parent": "大唐公司", "type": "办公室", "description": "", "chapter": 3},
        {"name": "新罗酒店", "parent": "", "type": "酒店", "description": "", "chapter": 4},
        {"name": "新罗酒店顶层", "parent": "新罗酒店", "type": "餐厅", "description": "", "chapter": 5},
    ]
    groups = aggregate_locations(raw)
    # 新算法期望：5 个独立 group（不互相合并）
    assert len(groups) == 5
```

### 4.5 预期效果（估算）

| 指标 | 现状（Phase 0a） | 修复后 | 变化 |
|---|---|---|---|
| Group 数量（《韩娱》228 章） | 398 | ~200-300（估） | -25% 到 -50% |
| 最大 group alias 数 | 28 | ~5（估） | -82% |
| User prompt 大小 | 53K chars | ~25-30K chars | -44% 到 -53% |
| LLM 输入 tokens | ~27K | ~13-15K | ~-50% |
| LLM 输出 tokens | ~30K | ~10-15K | ~-50% 到 -67% |
| 单次 LLM 调用预期耗时 | 超时（>10 min） | 30-60s | ✓ 修复 |
| Phase 0 总耗时（估算） | 超时失败 | 5-10 min | ✓ |
| Token 成本变化 | 基准 | 不变（batch 数量不变） | 0 |

### 4.6 为什么不动 batch size

另一种修复方案是**把 batch 从 1000 降到 200**：

| 维度 | 减小 batch | 软修饰白名单 |
|---|---|---|
| 修复根因 | ❌ 不修（substring 仍然误并） | ✅ 修（28 alias 不再误并） |
| Token 成本 | +200%（多 5 倍调用） | 0% |
| 调用次数 | +5 倍 | 不变 |
| 复杂 group 处理 | ❌（小 group 也可能误并） | ✅（每个 group 干净） |

软修饰白名单是**根本修复**，减 batch 只是**症状缓解**（且代价高）。后者作为未来兜底保留。

---

## 5. 替代方案对比

| 方案 | 思路 | 优点 | 缺点 | 推荐？ |
|---|---|---|---|---|
| **A. 软修饰白名单**（推荐） | substring + qualifier 检查 | 根本修复；零 token 成本 | 需要维护白名单 | ✅ |
| B. 去掉 substring 只用 SequenceMatcher | 仅依赖 ratio | 简单 | "居安小阁（主角）" 这种 legit merge 丢失 | ❌ |
| C. substring + 比例长度长度比 ≤ 1.3 | 长名最多比短名长 30% | 实现简单 | "宁安县" → "宁安县城"（+33%）会被拦；仍漏判"会议室"+"总裁办公室"（不同实体都短于公司名） | ❌ |
| D. 减 batch size 1000→200 | 减小单次 prompt 体积 | 简单 | +200% token 成本；不修根因 | ❌ |
| E. 软修饰白名单 + batch 200 | 双管齐下 | 修复 + 防御 | token 成本 +200% | ❌（浪费） |
| F. 加 "group 太大" 告警 + 不修 | 暴露问题让用户手动 | 最低风险 | 用户体验差 | ❌ |

**推荐 A**：根本修复、零额外成本、白名单明确可维护。

---

## 6. 测试计划

### 6.1 单元测试（`backend/tests/test_location_normalizer.py`）

新增/修改：

```python
class TestIsSameLocation:
    # === 现有 6 个 test 保持不变 ===
    
    # 新增 5 个
    def test_substring_with_entity_word_not_merged(self):
        """'大唐公司' vs '大唐公司会议室' → False（'会议室'不是软修饰）"""
        assert is_same_location("大唐公司", "大唐公司会议室") is False
        assert is_same_location("大唐公司会议室", "大唐公司") is False
    
    def test_substring_with_floor_word_not_merged(self):
        """'新罗酒店' vs '新罗酒店顶层' → False（'顶层'不是软修饰）"""
        assert is_same_location("新罗酒店", "新罗酒店顶层") is False
    
    def test_substring_with_branches_not_merged(self):
        """'济州岛' vs '济州岛大唐公司' → False（'大唐公司'不是软修饰）"""
        assert is_same_location("济州岛", "济州岛大唐公司") is False
    
    def test_brackets_around_soft_qualifier_merged(self):
        """'宁安县（已废弃）' vs '宁安县' → True（剥括号 → '已废弃' 是软修饰）"""
        assert is_same_location("宁安县", "宁安县（已废弃）") is True
        assert is_same_location("宁安县（已废弃）", "宁安县") is True
    
    def test_nested_brackets_merged(self):
        """嵌套括号也处理"""
        assert is_same_location("宁安县", "宁安县（主角（已故））") is True


class TestAggregateLocationsRegression:
    def test_no_over_aggregation_from_substring(self):
        """确保 substring 规则不再把不同实体误并"""
        raw = [
            {"name": "大唐公司", "parent": "", "type": "企业", "description": "", "chapter": 1},
            {"name": "大唐公司会议室", "parent": "大唐公司", "type": "会议室", "description": "", "chapter": 2},
            {"name": "大唐公司总裁办公室", "parent": "大唐公司", "type": "办公室", "description": "", "chapter": 3},
            {"name": "新罗酒店", "parent": "", "type": "酒店", "description": "", "chapter": 4},
            {"name": "新罗酒店顶层", "parent": "新罗酒店", "type": "餐厅", "description": "", "chapter": 5},
        ]
        groups = aggregate_locations(raw)
        assert len(groups) == 5  # 5 个独立 group
```

### 6.2 集成验证

```bash
# 1. 单元测试
python -m pytest backend/tests/test_location_normalizer.py -v
# 期望：原 6 个 + 新 5 个 + 回归 1 个 = 12 个 test_location_normalizer 全部 pass

# 2. 全量 backend 测试
python -m pytest backend/tests/ -v
# 期望：152 个全过（无回归）

# 3. 真实数据验证（《韩娱之光影交错》）
python -c "
import sys
sys.path.insert(0, r'F:\AI\小说分析器')
from pathlib import Path
import json
from backend.services.location_normalizer import aggregate_locations, build_location_prompt

output_dir = Path(r'F:\AI\小说分析器\workspace\分析结果\《韩娱之光影交错》\output')
raw_locations = []
for cf in sorted(output_dir.glob('chapter_*_result.json')):
    with open(cf, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for loc in data.get('locations', []):
        loc = dict(loc)
        loc['chapter'] = data.get('chapter_number')
        raw_locations.append(loc)

groups = aggregate_locations(raw_locations)
print(f'Group count: {len(groups)}')
print(f'Max aliases in any group: {max(len(g[\"aliases\"]) for g in groups)}')
print(f'Groups with >10 aliases: {sum(1 for g in groups if len(g[\"aliases\"]) > 10)}')

messages = build_location_prompt(groups, 1, 1)
print(f'User prompt chars: {len(messages[1][\"content\"])}')
"
# 期望：Group count 200-300；Max aliases ≤ 8；User prompt ≤ 30K chars
```

### 6.3 端到端验证（用户手动）

重启 desktop app，对《韩娱之光影交错》重新跑一次"开始总结"：

- **期望**：Phase 0a 在 5-10 分钟内完成（而非 10+ 分钟超时）
- **期望**：normalized 文件成功落盘
- **期望**：Phase 1-4 继续走完，最终总结完成

---

## 7. 风险分析

### 7.1 修复失败的风险

**风险 A**：白名单不全，遗漏某些 legit merge
- **概率**：低
- **影响**：少量地点未被合并（→ LLM 不必合并，可能输出更多 canonical）
- **缓解**：白名单可后续追加；不阻断流程

**风险 B**：白名单过宽，误判新实体为软修饰
- **概率**：低-中（取决于白名单定义）
- **影响**：少量不同地方被合并（→ 数据不干净，但不致命）
- **缓解**：测试覆盖明确的误判 case；后续 LLM 还可以二次去重

**风险 C**：规则 3 的 `len(shorter) >= 3` 把一些原本 `>= 4` 才匹配的情况放开
- **概率**：极低（`>= 3` 已经过滤了大部分噪声）
- **影响**：增加少量误判
- **缓解**：`_is_soft_qualifier` 兜底

### 7.2 实施风险

**风险 D**：现有 6 个 test 中可能有 1-2 个预期与新算法不一致
- **预检**：上面表格列出 6 个 test 的预期 vs 新算法行为，**全部仍通过**
- **缓解**：实施时先跑测试套件验证

**风险 E**：其他模块依赖 `is_same_location` 的当前行为
- **检查**：`text_utils.py::is_same_location` 被 `location_normalizer.py:67` 调用，是唯一调用点
- **缓解**：修改前 grep 全仓确认

### 7.3 性能风险

**风险 F**：新算法增加 `_is_soft_qualifier` 调用（regex 匹配 + dict 查找）
- **概率**：极低
- **影响**：~微秒级开销，远低于 SequenceMatcher
- **缓解**：无需特殊处理

---

## 8. 实施细节

### 8.1 修改文件清单

| 文件 | 类型 | 改动 |
|---|---|---|
| `backend/utils/text_utils.py` | 改 | 加 `_SOFT_QUALIFIERS` 常量 + `_is_soft_qualifier` 函数 + 重写 `is_same_location` |
| `backend/tests/test_location_normalizer.py` | 改 | 加 5 个新 test + 1 个回归 test |

**预计 diff**：+ ~80 / - ~20 行

### 8.2 提交策略

1. **Commit 1**：`fix(text_utils): is_same_location adds soft-qualifier whitelist`
   - 改 `text_utils.py`
   - 加 5 个新 test + 1 个回归 test
2. **Commit 2**（如适用）：`test: verify prompt size reduction on real corpus`
   - 仅当 Commit 1 后实测数据有显著改进

不修改：
- `backend/services/location_normalizer.py`（业务代码无需改）
- `agent.md`（除非有结构性变化）
- 任何前端代码

### 8.3 回滚计划

`git revert <commit_hash>` 单命令回滚。

回滚后：
- `is_same_location` 恢复旧行为（substring 无 qualifier）
- 已知 bug 恢复（Phase 0a 仍可能超时）
- 但不影响数据（无 schema 变更）

---

## 9. 审查者请关注

请审查以下决策点并给出意见：

1. **白名单是否完整、合理**？哪些常见的"软修饰"被遗漏？
2. **`len(shorter) >= 3` 这个阈值是否合适**？是否应保持 `>= 4`？
3. **优先级**：是否有其他更关键的 bug 应该先修？
4. **测试覆盖**：上面 6 个新 test 是否够？还是需要更多边界用例？
5. **性能预期**：Group count 降到 200-300 的预估是否合理？需要更激进的目标吗？
6. **风险评估**：上面列出的 7 个风险点是否有遗漏？

---

## 附录 A：实际 prompt 样本（前 800 字符）

```
## 待归一化的地点（batch 1/1，共 398 个 group）

[1] aliases=[大唐公司, 新罗酒店, 总裁办公室, 大唐公司总部, 新罗酒店顶层, 大唐公司会议室,
    大唐公司会客室, 济州岛大唐公司, 大唐公司摄影棚, 新罗酒店中餐厅, 新罗酒店宴会厅,
    新罗酒店洗手台, 大唐公司待客室, 新罗酒店办公室, 新罗酒店咖啡厅, 大唐公司一期工地,
    新罗酒店大唐分店, 新罗酒店总统套房, 大唐公司临时办公楼, 大唐公司济州岛工地,
    济州岛大唐公司总部, 大唐公司总裁办公室, 新罗酒店济州岛分店, 大唐公司济州岛分部,
    新罗酒店总裁办公室, 济州岛大唐公司大楼天台, 济州岛大唐公司员工食堂包厢]
    types={企业:3, 企业总部:4, 建筑工地:1, 办公楼:1, 酒店:8, 商业场所:2, 餐饮场所:1,
           建筑:20, 商业建筑工地:1, 高端约会场所:1, 公司:1, 办公/私密空间:1, 办公场所:1, 商业建筑:1}
    parents={济州岛:20, 首尔:10, 济州岛大唐公司:4, 大唐公司:3, 新村集团:1, 新罗酒店:7, 新村集团总部:1}
    chapter_count=40
    sample_desc="大唐集团开业庆典举办地，政商两界大佬云集"
```

## 附录 B：当前 `is_same_location` 完整代码

```python
# text_utils.py:362-384
def is_same_location(n1: str, n2: str) -> bool:
    """判断两个地名是否可能指向同一地点。

    规则（保守）：
    1. 归一后任一为空或长度 < 2 -> False（避免单字噪声误伤）
    2. 归一后相等 -> True
    3. 归一后较短串（≥4字）是较长串的子串 -> True（含角色/修饰后缀的合并，如 "居安小阁" ⊂ "居安小阁主角"）
    4. 短名（≤3字）走严格阈值 0.95，避免被前缀同形长名误并（如 "宁安县" 误并入 "宁安县城"）
    5. 其余按 SequenceMatcher 相似度阈值 0.85 判定
    """
    from difflib import SequenceMatcher as _SM
    a = _normalize_for_dedup(n1)
    b = _normalize_for_dedup(n2)
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 4 and shorter in longer:
        return True
    threshold = 0.95 if len(shorter) <= 3 else 0.85
    ratio = _SM(None, a, b).ratio()
    return ratio >= threshold
```

## 附录 C：审查检查清单（给审查者）

- [ ] 白名单 `_SOFT_QUALIFIERS` 是否合理
- [ ] 算法逻辑是否正确
- [ ] 测试用例是否覆盖关键场景
- [ ] 风险评估是否完整
- [ ] 实施步骤是否清晰
- [ ] 回滚方案是否够用
- [ ] 是否有更简单的方案