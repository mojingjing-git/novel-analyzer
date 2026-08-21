# Phase 0 归一化修复 — 外部审查文档（修订版 v2）

**日期**：2026-08-21（v2：经外部审查后拆分两个独立修复）
**作者**：AI Agent（小说分析器项目 Phase 0 SDD 维护）
**审查目的**：在合并修复之前获得独立技术审查
**状态**：等待审查

---

## 0. TL;DR（修订）

**两个独立问题被混为一谈，v1 spec 已废弃**：

| 问题 | 根因 | 修复 | 优先级 |
|---|---|---|---|
| **A. Phase 0a 超时** | `_LOCATIONS_BATCH_SIZE=1000` 把 398 group 塞 1 个 batch → 54K chars → 硬超时 | **确定性字符预算**（每批 ≤ 35K chars），与合并规则解耦 | **一线**（先修这个） |
| **B. 合并过松** | `is_same_location` 的 substring 规则 + 旧后缀白名单把"会议室/顶层"等实体词误并入主名 | **对称剥离前后缀修饰符**，前后缀都覆盖 | **二线**（修了 A 后视效果决定是否必要） |

**两个修复独立可测、独立可回滚**。不互相依赖。

---

## 1. 背景

### 1.1 项目概况

**小说智能分析器**（`F:\AI\小说分析器`）是用 LLM 分析长篇中文网络小说的工具。FastAPI + Vue 3 + pywebview 桌面应用。

### 1.2 Phase 0 归一化

2026-08-21 SDD 流程新增的归一化功能（在最终总结阶段前跑）：
- **0a-batches**：locations 切片 → 并发 LLM 调 → 校验 canonical 在 aliases 中
- **0a-consol**：1 次 LLM 合并跨 batch 同地点
- **0b-batches**：spatial 切片 + canonical 白名单 → 并发 LLM 调
- **0b-dedupe**：机械去重

设计目的是解决 LLM 生成 `chapter_*.json` 时**地名 / type / parent 不一致**的问题。

### 1.3 `is_same_location` 的作用

在 `aggregate_locations` 的预聚合阶段用 Union-Find 把相似地名合并为同一 group。Task 1 实现（commit `69bb930`）。

### 1.4 `_LOCATIONS_BATCH_SIZE` 的作用

```python
# location_normalizer.py:36-37
_LOCATIONS_BATCH_SIZE = 1000
```

控制每个 LLM batch 包含多少 location group。

---

## 2. 问题 1：Phase 0a 超时（10+ 分钟）

### 2.1 症状

- 用户报告"归一化失败直接停止了"
- 日志显示 batch 1 跑了 10.5 分钟才被硬超时

### 2.2 日志证据

```
22:20:52 [INFO] Phase 0a: 398 个 location group
22:20:52 [INFO] openai SDK POST /v1/chat/completions
22:30:52 [INFO] openai._base_client: Retrying request in 0.424152 seconds
22:31:22 [WARNING] 请求硬超时(timeout=630s): API 无响应
22:31:22 [ERROR] Phase 0a batch 1 失败
```

### 2.3 实测 prompt 体积

```
System prompt:    671 chars (~335 tokens)
User prompt:    53,388 chars (~26,700 tokens)
Expected output: ~59,700 chars (~29,850 tokens)
Total per call:  ~57,000 tokens
```

### 2.4 根因

**`_LOCATIONS_BATCH_SIZE=1000` 太大**：
- 《韩娱之光影交错》228 章产生 398 个 unique location group
- BATCH_SIZE=1000 > 398 → 全部塞 1 个 batch
- 单 batch prompt 54K chars → LLM 调用超过 630s 硬超时

**与合并规则无关**：
- 即使 `is_same_location` 是完美的（每个 group 只 1 个 alias），398 group 仍然塞 1 个 batch
- 唯一的根治办法是**让 batch 不超过字符预算上限**，而不是靠合并减小体积

### 2.5 修复方案 1：确定性字符预算

**位置**：`backend/services/location_normalizer.py`

**逻辑改动**：把 `_split_into_batches` 从"按 group 数切"改成"按 prompt 字符预算切"。

```python
# 替换 _LOCATIONS_BATCH_SIZE = 1000  → 改为按字符预算
_LOCATION_PROMPT_BUDGET_CHARS = 35000   # 每批 user prompt ≤ 35K chars（约 11-12K tokens，留余量给输出）
_LOCATION_BATCH_MAX_GROUPS = 1000       # 兜底，避免单批 group 太多（防御性）


def _estimate_group_chars(g: dict) -> int:
    """估算单个 group 在 user prompt 中的字符数（用于 batch 切分）"""
    return (
        sum(len(a) for a in g.get("aliases", [])) + 20 +  # aliases + 括号
        sum(len(k) + len(v) + 5 for k, v in g.get("types", {}).items()) + 20 +
        sum(len(k) + len(v) + 5 for k, v in g.get("parents", {}).items()) + 20 +
        80  # 固定字段（chapter_count + sample_desc + 字段标签）
    )


def _split_into_batches_by_budget(groups: list, budget: int, max_groups: int) -> list:
    """按 user prompt 字符预算切分批次"""
    batches = []
    current = []
    current_chars = 0
    for g in groups:
        g_chars = _estimate_group_chars(g)
        if current and (current_chars + g_chars > budget or len(current) >= max_groups):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(g)
        current_chars += g_chars
    if current:
        batches.append(current)
    return batches
```

**`_run_phase_0a_batches` 替换**：

```python
async def _run_phase_0a_batches(
    self, groups: List[Dict[str, Any]]
) -> Optional[List[Dict[str, Any]]]:
    # 替换原来的 self._split_into_batches(groups, self.locations_batch_size)
    # 用按预算切的版本（保持调用位置不变）
    batches = _split_into_batches_by_budget(
        groups,
        budget=_LOCATION_PROMPT_BUDGET_CHARS,
        max_groups=_LOCATION_BATCH_MAX_GROUPS,
    )
    logger.info(f"Phase 0a：{len(groups)} groups → {len(batches)} batches（按字符预算）")
    semaphore = asyncio.Semaphore(self.concurrency)
    # ... 其余逻辑不变
```

**注意**：原有的 `LocationNormalizer._split_into_batches` 静态方法在 Commit 1 中**删除**（被模块级函数替代）。如果其他地方（如 `_run_phase_0b_batches`）也调用它，需要相应更新。但 `_run_phase_0b_batches` 用的是 `spatial_batch_size`，仍可保留原 `_split_into_batches` 逻辑，或为其也加预算版。**为最小改动，仅替换 locations 一处；spatial 保持原 `_split_into_batches` 调用。**

### 2.6 测试方案

```python
def test_split_batches_by_budget_respects_char_limit(self):
    """每批 user prompt chars ≤ 预算上限"""
    groups = [{"aliases": ["x" * 100], "types": {"y": 1}, "parents": {"z": 1}, "chapter_count": 1, "sample_desc": ""} for _ in range(1000)]
    batches = _split_into_batches_by_budget(groups, budget=10000, max_groups=1000)
    for b in batches:
        chars = sum(_estimate_group_chars(g) for g in b)
        assert chars <= 10000

def test_split_batches_by_budget_respects_group_limit(self):
    """防御性：单批 group 数不超过 max_groups"""
    groups = [...] * 5000
    batches = _split_into_batches_by_budget(groups, budget=1_000_000, max_groups=500)
    assert all(len(b) <= 500 for b in batches)

def test_split_batches_single_batch_when_under_budget(self):
    """未超预算时保持 1 batch"""
    groups = [{"aliases": ["短"], "types": {}, "parents": {}, "chapter_count": 1, "sample_desc": ""}] * 50
    batches = _split_into_batches_by_budget(groups, budget=10000, max_groups=1000)
    assert len(batches) == 1
    assert len(batches[0]) == 50
```

### 2.7 预期效果

| 指标 | 现状 | 修复后 |
|---|---|---|
| 《韩娱》398 group 的 batch 数 | 1（全塞 1 个） | ~2（按 35K 字符切） |
| 最大单 batch user prompt | 53K chars | ≤ 35K chars |
| 单次 LLM 调用预期耗时 | 超时 >10 min | 30-60s |
| Phase 0a 总耗时 | 超时失败 | ~1-2 min（2 batches 并发） |

### 2.8 风险

- **风险 A1**：字符估算公式不准导致实际超预算
  - **缓解**：保守预算（35K 而非 50K），预留 30% 余量给输出 + system prompt
- **风险 A2**：并发 2 batch 撞 API 限流
  - **缓解**：`summary_concurrency=2`（已有），且 batch 间独立（不会卡住）

---

## 3. 问题 2：`is_same_location` 过合并（数据正确性）

### 3.1 症状

**与超时无关**，纯粹是数据质量问题：

- `"大唐公司"` 与 `"大唐公司会议室"` 被并入同一 group
- `"新罗酒店"` 与 `"新罗酒店顶层"` 被并入同一 group
- `"济州岛"` 与 `"济州岛大唐公司"` 被并入同一 group

合并后 group 包含 28 个 alias（其中至少 6 个不同地方），LLM 无法选出合理的 canonical。

### 3.2 根因分析

**当前算法**（`text_utils.py:362-384`）：
```python
if len(shorter) >= 4 and shorter in longer:
    return True   # ← substring 命中即合并
```

`is_same_location("大唐公司", "大唐公司会议室")` → substring 命中 → True → 合并。

**注意**：当前生产代码**没有白名单**，substring 命中即合并。v1 spec 提出的"软修饰白名单"是未落地的提案；当前合并纯粹因为 substring 规则，不是因为白名单缺失。

白名单路径（v1 spec 提出、从未落地）的不可行性：
- extra = `longer[len(shorter):]` = `"会议室"`
- 若有白名单：`"会议室" not in 白名单` → False → 不合并 ✓

**但白名单只能识别后缀**——前缀修饰无法识别：
- `is_same_location("新大唐公司", "大唐公司")` → shorter="大唐公司" 在 longer="新大唐公司" 中 → True
- extra = `longer[len(shorter):]` = `"新大唐公司"[4:]` = `"司"`
- `"司" not in 白名单` → 不合并 ✗（**应该合并**）

前缀修饰"新大唐公司/老大唐公司"被静默漏判。

### 3.3 修复方案 2：对称剥离前后缀修饰符

**核心思路**：不要比较"长名相对短名多什么"，而是**两端都剥掉已知修饰符后比核心**。

**位置**：`backend/utils/text_utils.py`

```python
# 软修饰词表（按位置分类）
_PREFIX_MODS = (
    "新", "老", "旧", "原",          # 短前缀
    "原址", "旧址",                    # 长前缀
)
_SUFFIX_MODS = (
    "主角", "身边", "附近", "一带", "境内", "内部",
    "已废弃",
    "新址",   # 与前缀 "原址"/"旧址" 对称；否则"宁安县新址"漏判
)


def _strip_location_mods(name: str) -> str:
    """从 name 两端剥离软修饰符（角色后缀/状态等）

    循环剥离直到稳定，因为前缀可能连续出现（如"旧原址X" → "原址X" → "X"）
    """
    s = name
    while True:
        stripped = False
        # 前缀
        for p in _PREFIX_MODS:
            if s.startswith(p) and len(s) > len(p) + 1:
                s = s[len(p):]
                stripped = True
                break
        if stripped:
            continue
        # 后缀
        for x in _SUFFIX_MODS:
            if s.endswith(x) and len(s) > len(x) + 1:
                s = s[:-len(x)]
                stripped = True
                break
        if not stripped:
            break
    return s


def is_same_location(n1: str, n2: str) -> bool:
    """判断两个地名是否指向同一地点

    规则：
    1. 归一化后任一为空或长度 < 2 → False（避免单字噪声）
    2. 归一化后完全相等 → True
    3. 两端剥离已知前后缀修饰符后核心相同 → True

    注：归一化阶段（_normalize_for_dedup）已剥离停用词和标点（如"（"），
    此处只处理语义修饰词。
    """
    a = _normalize_for_dedup(n1)
    b = _normalize_for_dedup(n2)
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    return _strip_location_mods(a) == _strip_location_mods(b)
```

**对照现有 6 个 test**（逐 case 已核对）：

| 用例 | 输入 → normalize | strip → 比核心 | 期望 | 结果 |
|---|---|---|---|---|
| `"宁安县"` / `"宁安县"` | `"宁安县"` / `"宁安县"` | `"宁安县"` / `"宁安县"` | True | ✓ True |
| `"宁安县"` / `"宁安县。"` | `"宁安县"` / `"宁安县"` | 同上 | True | ✓ True |
| `"宁安县"` / `"宁安县城"` | `"宁安县"` / `"宁安县城"` | `"宁安县"` / `"宁安县城"` | False | ✓ False |
| `"居安小阁"` / `"居安小阁（主角）"` | `"居安小阁"` / `"居安小阁主角"` | `"居安小阁"` / `"居安小阁"` | True | ✓ True |
| `"大贞"` / `"大秀"` | `"大贞"` / `"大秀"` | `"大贞"` / `"大秀"` | False | ✓ False |
| `"京"` / `"京"` | `"京"` / `"京"` | len<2 早退 | False | ✓ False |

**新增 test case**（覆盖前缀修饰）：

```python
def test_prefix_modifier_merged(self):
    """前缀软修饰（新/老/旧/原/原址/旧址）也应识别"""
    assert is_same_location("大唐公司", "新大唐公司") is True
    assert is_same_location("大唐公司", "老大唐公司") is True
    assert is_same_location("宁安县", "旧宁安县") is True

def test_no_entity_word_merge(self):
    """实体词（会议室/顶层/总部）不合并"""
    assert is_same_location("大唐公司", "大唐公司会议室") is False
    assert is_same_location("新罗酒店", "新罗酒店顶层") is False

def test_chained_prefix_modifier(self):
    """连续前缀修饰应递归剥离"""
    assert is_same_location("宁安县", "旧原宁安县") is True
    # 但要防止无限循环
    assert is_same_location("宁安县", "原旧原旧宁安县") is True  # 仍 True，但不应无限递归
```

### 3.4 预期效果

| 输入 | 旧算法 | 新算法 | 评价 |
|---|---|---|---|
| `"大唐公司"` / `"大唐公司会议室"` | 误并（substring）| 不并（"会议室"不是修饰符）| ✓ |
| `"新大唐公司"` / `"大唐公司"` | 误并（substring + 后缀"司"不在白名单）| **并**（"新"是对称前缀修饰符）| ✓ |
| `"居安小阁"` / `"居安小阁（主角）"` | 并 | 并 | ✓ |
| `"宁安县"` / `"宁安县城"` | 不并（0.857 ratio + substring + "城"不在旧白名单）| 不并（"城"不是修饰符）| ✓ |
| `"宁安县"` / `"新宁安县"` | 不并（旧 substring extra="新"不在白名单 + ratio 0.667）| **并**（"新"是对称前缀修饰符）| ✓ |

### 3.5 风险

- **风险 B1**：白名单不全，遗漏常见修饰词（如"旧址"应该是后缀还是前缀？混淆"原址""旧址"归属）
  - **缓解**：白名单可后续追加；漏判只是少合并（数据略多但不致命）
- **风险 B2**：循环剥离导致死循环
  - **缓解**：每次剥离后 `len(s)` 严格减小（strip 后 s 缩短），最多循环 O(|name|) 次
- **风险 B3**：递归剥前缀可能过剥（如"宁安县"→"宁安县"如果开头是"宁"……）
  - **缓解**：白名单是精确字符串匹配（不是 prefix 匹配），"宁"不在白名单所以不会剥

### 3.6 `_PREFIX_MODS` 与 `_SUFFIX_MODS` 的对称性观察

**已采取的补救**：`_SUFFIX_MODS` 加了 `"新址"`，与 `_PREFIX_MODS` 的 `"原址"/"旧址"` 大致对称。

实际场景覆盖：
- `"宁安县原址"` → strip "原址"（前缀）→ `"宁安县"` ✓
- `"宁安县旧址"` → strip "旧址"（前缀）→ `"宁安县"` ✓
- `"宁安县新址"` → strip "新址"（后缀）→ `"宁安县"` ✓（v2 补的）

**残留不对称**：`_SUFFIX_MODS` 没有 `"旧址"`（已被前缀占）。若场景出现 `"X旧址"`（如"宁安县旧址"已是前缀情况），会被剥前缀而不是后缀，**结果相同**（都是 → `"宁安县"`），不影响。

**无更严重的不对称**。审查者如希望加更多复合词可后续追加。

---

## 4. 实施计划

### 4.1 改动文件

| 文件 | 改动 |
|---|---|
| `backend/services/location_normalizer.py` | 加 `_LOCATION_PROMPT_BUDGET_CHARS`、`_LOCATION_BATCH_MAX_GROUPS`；改 `_split_into_batches` 为按预算切；`_run_phase_0a_batches` 调用新函数 |
| `backend/utils/text_utils.py` | 加 `_PREFIX_MODS`、`_SUFFIX_MODS`、`_strip_location_mods`；重写 `is_same_location` |
| `backend/tests/test_location_normalizer.py` | 加 3+ 个 batch split test + 3 个 `is_same_location` test |

### 4.2 提交策略

**Commit 1**：`fix(location_normalizer): batch by prompt char budget, not group count`
- 只改 `location_normalizer.py`
- 加 3 个 batch split test
- 独立可测：旧 6 个 + 新 3 个 = 9 个 test 全过

**Commit 2**：`fix(text_utils): is_same_location strips prefix and suffix modifiers symmetrically`
- 只改 `text_utils.py`
- 加 3 个 `is_same_location` test
- 独立可测：旧 6 个 + 新 3 个 = 9 个 test 全过

**两个 commit 独立可回滚**。如果 Fix 2 效果不理想或测试不通过，单独 revert Fix 2 不影响 Fix 1。

### 4.3 验证

```bash
# Commit 1 后
python -m pytest backend/tests/test_location_normalizer.py -v
# 期望：所有现有 test + 3 个新 batch split test 全过

# Commit 2 后
python -m pytest backend/tests/test_location_normalizer.py -v
# 期望：所有现有 test + 3 个新 is_same_location test 全过

# 全量
python -m pytest backend/tests/ -v
# 期望：158 passed（152 现有 + 3 batch split test + 3 is_same_location test）

# 真实数据验证
python -c "
from pathlib import Path
import json, sys
sys.path.insert(0, r'F:\AI\小说分析器')
from backend.services.location_normalizer import (
    _split_into_batches_by_budget, _LOCATION_PROMPT_BUDGET_CHARS,
    aggregate_locations,
)

output_dir = Path(r'F:\AI\小说分析器\workspace\分析结果\《韩娱之光影交错》\output')
raw = []
for cf in sorted(output_dir.glob('chapter_*_result.json')):
    with open(cf, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for loc in data.get('locations', []):
        loc = dict(loc); loc['chapter'] = data.get('chapter_number')
        raw.append(loc)

groups = aggregate_locations(raw)
batches = _split_into_batches_by_budget(groups, _LOCATION_PROMPT_BUDGET_CHARS, 1000)
print(f'398 groups → {len(batches)} batches')
for i, b in enumerate(batches):
    chars = sum(_estimate_group_chars(g) for g in b)
    print(f'  batch {i+1}: {len(b)} groups, ~{chars} chars')
"
```

### 4.4 用户端到端验证

1. 重启 desktop app
2. 对《韩娱之光影交错》点"开始总结"
3. 期望 Phase 0a 在 5-10 分钟内完成（而非超时）
4. 期望最终总结报告生成
5. 期望 normalized JSON 文件落盘（打开 `output/locations_normalized.json` 看是否合并了"会议室"等）

### 4.5 回滚

两个 commit 独立回滚：
- `git revert <commit_hash>` 单命令回滚
- 回滚后 Phase 0 行为退化到当前（超时 + 误并）

---

## 5. 风险汇总

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Fix A 字符估算不准 | 低 | 单 batch 仍可能略超预算 | 35K 保守值 + 留余量 |
| Fix A 并发限流 | 低 | API 拒绝 | summary_concurrency=2 |
| Fix B 白名单不全 | 中 | 少量应合并未合并 | 白名单可后续追加 |
| Fix B 递归死循环 | 极低 | 程序卡住 | 每次 strip len 必减 |
| Fix B 前缀/后缀不对称 | 中 | "新址"等场景漏判 | 审查者决策 |
| Commit 顺序依赖 | 极低 | 一个坏 commit 影响另一个 | 两个 commit 独立可回滚 |

---

## 6. 审查者请关注

请审查以下决策点并给出意见：

1. **批预算值 35K chars**：基于《韩娱》样本（53K → 期望 ≤ 35K）。是否合理？过低会导致 batch 数过多、过宽会重新超时。
2. **白名单范围**：`_PREFIX_MODS` = 新/老/旧/原/原址/旧址 + `_SUFFIX_MODS` = 主角/身边/附近/一带/境内/内部/已废弃。是否完整？是否遗漏常用修饰？
3. **不对称问题**：`_PREFIX_MODS` 有"原址/旧址"但 `_SUFFIX_MODS` 没"新址/旧址"，是否需要补充？
4. **`len(s) > len(p) + 1` 阈值**：strip 后剩余至少 2 字符才允许剥。是否过严？
5. **Commit 拆分**：先 Fix A（批预算）再 Fix B（合并），还是合并成一个？分开是否合理？
6. **测试覆盖**：上面的新 test case 是否够？还需要加什么边界？

---

## 附录 A：实际 prompt 样本（前 800 字符）

```
## 待归一化的地点（batch 1/1，共 398 个 group）

[1] aliases=[大唐公司, 新罗酒店, 总裁办公室, 大唐公司总部, 新罗酒店顶层, 大唐公司会议室,
    大唐公司会客室, 济州岛大唐公司, 大唐公司摄影棚, 新罗酒店中餐厅, ...]   ← 28 个 alias
    types={企业:3, 企业总部:4, 建筑工地:1, 办公楼:1, 酒店:8, 商业场所:2, ...}    ← 13 种 type
    parents={济州岛:20, 首尔:10, 济州岛大唐公司:4, 大唐公司:3, ...}              ← 7 个 parent
    chapter_count=40
```

## 附录 B：当前 `_LOCATIONS_BATCH_SIZE` 与 `is_same_location` 完整代码

```python
# location_normalizer.py:36-37
_LOCATIONS_BATCH_SIZE = 1000
_SPATIAL_BATCH_SIZE = 1000
_CONSOL_BATCH_SIZE = 2000

# location_normalizer.py:559-563
batches = self._split_into_batches(groups, self.locations_batch_size)

# text_utils.py:362-384（v1 实现）
def is_same_location(n1, n2):
    if n1 == n2: return True
    a = _normalize_for_dedup(n1)
    b = _normalize_for_dedup(n2)
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    if a == b: return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 4 and shorter in longer:
        return True                                            # ← 问题
    threshold = 0.95 if len(shorter) <= 3 else 0.85
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold
```

## 附录 C：审查检查清单（给审查者）

- [ ] 35K 字符预算是否合理
- [ ] `_PREFIX_MODS` + `_SUFFIX_MODS` 白名单是否完整
- [ ] 前缀/后缀不对称（"原址"/"旧址" vs 无"新址"/"旧址"）是否需要修复
- [ ] `len(s) > len(p) + 1` 阈值是否过严
- [ ] Commit 拆分是否合理（Fix A + Fix B 分开）
- [ ] 测试覆盖是否充分
- [ ] 是否有更简单的方案（不修复 / 合并 fix / 仅 Fix A）