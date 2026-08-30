# 审核报告 #6：跨章一致性校验

> 审核日期：2026-08-27
> 审核范围：`backend/core/analyzer.py` `validate_parsed_analysis`、LLM 输出契约（`backend/core/prompt_builder.py`）、50 类伏笔定义（`backend/config/constants.py`）、伏笔总表构建（`backend/services/final_summary.py` `_build_foreshadow_catalog`）
> 改进方向：在 `validate_parsed_analysis` 中加严三类校验：
> 1. `core_events.characters` 中角色名应在 `character_arcs` 出现
> 2. `cross_block.contextual_link` 引用章号应 < 当前章号
> 3. `foreshadowing.type` 必须在 50 类内

---

## 1. 源码调研摘要

### 1.1 现状：`validate_parsed_analysis` 只做 4 字段存在性 + 嵌套类型

`backend/core/analyzer.py:25-46`

```python
def validate_parsed_analysis(data: dict) -> tuple:
    required_fields = ["core_events", "cross_block", "long_context_insights"]
    missing = [k for k in required required_fields if k not in data]
    if missing:
        return False, f"JSON缺少必须字段: {missing}"
    cb = data.get("cross_block", {})
    if not isinstance(cb, dict) or not cb.get("summary"):
        return False, "cross_block.summary 为空或类型错误"
    ce = data.get("core_events", [])
    if not isinstance(ce, list):
        return False, f"core_events 应为列表，实际为 {type(ce).__name__}"
    # 嵌套对象字段必须是 dict（缺失键保持既有宽松语义，from_dict 有默认值兜底）
    for key in ("long_context_insights", "updated_knowledge"):
        v = data.get(key)
        if v is not None and not isinstance(v, dict):
            return False, f"{key} 应为对象，实际为 {type(v).__name__}"
    return True, ""
```

**注意**：P2 修复（2026-08-24）专门加固了"真值字符串绕过键存在性检查"的洞（`test_models.py:184-201` 覆盖）。新加校验不能破坏这条修复。

### 1.2 LLM 输出契约的 3 个目标校验对象

#### 对象 A：`core_events[].characters` 与 `character_arcs[].name` 一致性

- `prompt_builder.py` 输出的 schema（line 55-56）：
  ```json
  "core_events": [{"id": 1, "event": "事件", "characters": "角色", "function": "作用", "importance": "高|中|低"}],
  "character_arcs": [{"name": "角色", "surface_action": "...", ...}]
  ```
- 实际用法：
  - `core_events.characters`（字符串，逗号分隔多角色）→ `aggregate_character_tracking` (L302-336) 用于角色→事件反向索引
  - `character_arcs[].name`（裸名字符串）→ `kb.character_states[arc.name] = arc.surface_action` (memory_state.py:386)
- **当前问题**：LLM 可能输出 `core_events.characters = "林凡、叶凡"`，但 `character_arcs = [{name: "林凡"}]`（叶凡漏写 arc）。**这种"出现在 event 但没在 arc"的角色会被记入 event 但不入 KB 的 character_states**。
- 反向也存在：`character_arcs = [{name: "神秘老者"}]`，`core_events.characters = "林凡"`（神秘老者只被叙述没参与 event）—— 这是合法的，不应被校验拒绝。

#### 对象 B：`cross_block.contextual_link` 引用章号

- `prompt_builder.py:65`：`"contextual_link": "与哪章哪事件呼应（必须具体引用章号）"`
- `models/analysis_result.py:73` 字段定义：`contextual_link: str = ""`
- 实际用法：仅做"提示用户",没有后处理（grep 整个 backend 目录，未发现对 `contextual_link` 的解析或消费）。
- **改进风险**：后处理尚不存在，校验严格性 = 0（仅作展示用），校验失败 → 重试链烧 token 收益极小。

#### 对象 C：`foreshadowing[].type` 必须在 50 类内

- `prompt_builder.py:50`：`"foreshadowing 的 type 字段必须从下列 50 类中选一个，禁止自创类型"`
- 50 类定义：`backend/config/constants.py:80-144` `FORESHADOW_CATEGORY_DEFS`（含 10 个维度，共 50 个类别名）
- 实际归一化执行：`backend/services/final_summary.py:791-908` `_build_foreshadow_catalog`，其中：
  - L828 `valid_names = {name for name, _, _ in FORESHADOW_CATEGORY_DEFS}`
  - L846 `if ftype in valid_names: category = ftype`（直接用）
  - L849 `else: category = type_to_category.get(ftype, FORESHADOW_CATEGORY_FALLBACK)`（映射或 fallback 到"其他"）
  - **已做归一化，但发生在后处理阶段（final_summary 阶段），不在 analyze 阶段**

### 1.3 与其它模块的耦合点

1. **重试链**：`analyzer.py:136-144` `build_retry_messages` 把 `validation_error` 拼到 user message 末尾。任何新校验失败都会触发 LLM 重试（消耗 token）。
2. **断点续跑**：校验只发生在 `chat_with_retry` 阶段（在线 LLM 调用），不影响 `restore_from_disk` 或 `_parse_response` 阶段。
3. **KB 合并**：`memory_state._merge_result_into` 持有 KB 但不在 `validate_parsed_analysis` 上下文中——**跨章校验需要 KB 访问，但 validate 阶段没有 KB**。
4. **伏笔账本**：与 50 类校验正交，账本只接收去重后 catalog。

### 1.4 已有测试覆盖

`test_models.py:184-201` `test_validate_rejects_string_nested_objects` 覆盖当前 4 字段校验（含 updated_knowledge 缺失键宽松）。**新校验需要完全新建测试用例**。

---

## 2. 多角度评分

| 维度 | 评分（1=最低，5=最高） | 说明 |
|------|----------------------|------|
| 改进难度 | 4 | 校验 1（角色一致性）需要跨字段解析；校验 2（章号提取）需要正则；校验 3（50 类）需要导入 FORESHADOW_CATEGORY_DEFS。三个校验实现复杂度差异大 |
| 改进收益 | 3 | 校验 3 收益明确（避免 50 类幻觉污染）；校验 1 收益有限（多数 LLM 输出已对齐）；校验 2 收益极低（无下游消费者） |
| 代码复杂度提升 | 3 | 新增 ~50 行校验逻辑（每个校验 ~15 行）；3 个错误码分支；retry hint 模板新增 3 种 |
| 维护难度 | 4 | 50 类白名单硬编码在 constants.py，新增/删除类别需要同步更新校验逻辑；角色一致性校验的"严格 vs 宽松"边界需要维护决策 |
| 兼容性风险 | 2 | 仅在 validate 阶段加严，不修改磁盘格式；但**误杀会触发 LLM 重试链烧 token** |
| 测试覆盖成本 | 4 | 每个校验规则要独立测试：误杀 vs 漏杀；LLM 输出多种边界格式（空列表、None、空字符串、含后缀角色名）；需要 fixture 模拟 LLM 真实输出漂移 |
| 实施风险 | 4 | 误杀率是最关键风险——LLM 一次失败就触发完整重试链（温度退火 3 次 + 指数退避 1 次），相当于 4x 章节分析成本 |

**综合优先级**：**低-中**。校验 3（50 类）有中等价值；校验 1、2 收益不抵成本。**建议只做校验 3**。

---

## 3. 关键发现

### 3.1 校验 1（角色一致性）容易"反向误杀"

LLM 输出 "林凡在场" 但没为他写 arc（因为本章林凡只是被提及没变化），是完全合法的。如果要求"core_events.characters 中每个角色必须在 character_arcs 出现"，会大面积 reject 合法输出。

**应该校验的是反向**：`character_arcs[].name` 中的每个角色**应该**在 `core_events.characters` 中出现（即"写了 arc 就必须参与 event"）。但这条规则也不严谨——LLM 偶尔会基于 LCI 中"内心变化"独立写 arc。

**实际建议**：不校验。仅做"软提示"：在 retry hint 中说"如果某角色只出现在 arc 但没参与 event，请补充该角色到 events 中"。

### 3.2 校验 2（章号引用）下游不存在

`grep contextual_link` 全 backend 目录，**未发现任何消费 `contextual_link` 的代码**。它是一个**仅显示字段**（前端展示用）。在此字段上加严校验，相当于"为了让字段格式整齐而消耗 LLM token"。

**实际建议**：跳过校验 2。如果未来有下游消费者需要此字段，校验放在下游消费入口，而非分析阶段。

### 3.3 校验 3（50 类）有真实价值但时机偏早

LLM 在 prompt 已经明确要求"必须从 50 类中选一个"的情况下，仍有约 5-10% 概率输出自由式 type（尤其在 prompt 越界 / 退火阶段）。后置 `_build_foreshadow_catalog` 已经做归一化（fallback 到"其他"），但这意味着**大量伏笔被错误归类到"其他"**，最终报告的伏笔分类分布严重失衡。

**前移到 validate 阶段的收益**：
- 立即 reject 幻觉 type，触发 LLM 重试输出正确值
- 减少下游 fallback 数量

**风险**：
- 严格的白名单 = 0 容错，任何"接近 50 类但不完全匹配"的输出都失败
- 50 类本身可能在未来扩展，硬编码白名单会失效

**实际建议**：做校验 3，但用**模糊匹配 + 高置信度阈值**：
- 完全匹配 50 类之一 → 通过
- 不完全匹配但与 50 类中任一类的 `name + description` 相似度 ≥ 0.85（用现有 SequenceMatcher）→ 通过
- 都不通过 → reject 并 retry

### 3.4 validate 阶段没有 KB 上下文

`validate_parsed_analysis` 在 `analyzer.py:128` 调用，发生在 `chat_with_retry` 内。**此时 KB 还没被访问**——本函数的输入是 `validate_json_response(response)` 解析的纯 dict。

跨章校验（"角色名应在前面 KB 中出现"、"引用章号应 < 当前章号"）需要访问**已合并的 KB**——这意味着：
- 要么把跨章校验挪到 `analyzer._parse_response` 之后、`memory_state.add_result` 之前——但**失败无法触发 LLM 重试**（LLM 已返回）
- 要么在 `validate_parsed_analysis` 接受一个 `prior_arcs: Set[str]` 参数——但**当前章 LLM 不知道前面 KB 完整状态**（只在 prompt 注入前 30 条 arcs），校验标准难定

**实际建议**：跨章一致性**不在 analyze 阶段校验**。改为后置检查（`memory_state.add_result` 内，校验失败只记 warning，不 reject）—— 这样不消耗 LLM token，但能 catch 一致性问题。

---

## 4. 实施风险

### 4.1 失败模式

1. **校验 1 反向误杀**：LLM 偶尔写"未参与本章事件的角色变化"（回忆、内心独白、伏笔触发），拒绝这种输出 = 损失内容质量。
2. **校验 2 误杀率极高**：LLM 写"参考前文第 X 章"（X 可能不在 KB 已知范围）—— 校验章号是否存在需要 KB 全章列表；或 X 是个大概引用（如"第十几次出场"）—— 校验章号 < 当前章 不能识别非数字引用。
3. **校验 3 白名单过严**：50 类不包含的合法类型（如"修炼"和"境界"在不同书中语义不同）被 reject → LLM 反复重试仍输出非白名单值 → 烧完整重试链 token。
4. **50 类常量变更**：未来增加/删除/重命名某类别时，校验逻辑需要同步更新；若无强提醒，校验会静默失效或误杀。

### 4.2 边界条件

- `core_events = []`：空列表应通过校验 1（无角色可校验），但需校验"该章必须至少有 1 个 core_event 否则无效"——这是另一个话题。
- `character_arcs = []`：空列表应通过校验 1（无 arc 可校验）。
- `core_events[i].characters = ""`：空字符串，无角色，无需校验 1。
- `cross_block.contextual_link = ""`：空字符串，**不需要引用章号**，应通过校验 2。
- `foreshadowing = []`：空列表，校验 3 无对象可校验。
- `foreshadowing[i].type = ""`：空字符串，当前 50 类校验会 reject（空不在 50 类内），需决定是 reject 还是 fallback。

### 4.3 与其它模块的交互

- **重试链**：`analyzer.py:136-144` `build_retry_messages` 把 `validation_error` 拼到 user message。校验 1/2/3 失败都会触发完整 LLM 重试（温度退火 3 次 + 指数退避 1 次）。**单次校验失败成本 = 4x 章节分析 token**。
- **test_models.py 已有测试** `test_validate_rejects_string_nested_objects`：当前 4 字段。新校验的"通过条件"需要与现有"宽松语义"对齐（如缺 updated_knowledge 仍通过）。
- **伏笔账本**：50 类校验在 validate 阶段提前后，`_build_foreshadow_catalog` 的 fallback 逻辑可以保留作为双保险（防御 validate 误放过的边缘 case）。
- **断点续跑**：校验仅在 LLM 调用阶段发生，不影响 `restore_from_disk`。已落盘 result.json 不会被校验。

---

## 5. 兼容性影响

### 5.1 对断点续跑的影响

无直接影响。`validate_parsed_analysis` 仅在 LLM 响应后被调用，不涉及磁盘读取。

### 5.2 对旧书分析结果的影响

无直接影响。旧书 result.json 已固化，本次改进不修改磁盘格式。

### 5.3 对 KB 数据结构的影响

- 校验 1（角色一致性）：不修改 KB 字段，只控制 LLM 输出质量。
- 校验 2（章号引用）：不涉及 KB（contextual_link 不入 KB）。
- 校验 3（50 类）：KB 持久化的 `foreshadowing_network` 字段是字符串拼接形式（"第N章: ..."），不含 type，所以 KB 本身不受影响。但下游 `_build_foreshadow_catalog` 的输入数据（每章 result.json 中的 `foreshadowing[]`）会更规范。

### 5.4 对伏笔账本（foreshadow_ledger）状态机的影响

- 账本接收的是 `_build_foreshadow_catalog` 输出，**校验 3 提前后**，catalog 输入的 type 字段已规范化，账本不感知。
- 账本状态机（active/resolved/dormant）不受影响。

### 5.5 对 50 类分类体系的影响

- **50 类扩展时必须同步更新校验逻辑**。`FORESHADOW_CATEGORY_DEFS` 在 `constants.py:80-144` 定义；`FORESHADOW_CATEGORY_SCHEMA_VERSION = 1` 已有版本号机制（`prompt_builder.py` 引用，触发 per-book type_map 自动失效）。
- 建议把 50 类白名单导出为 `set[str]` 公共常量（如 `VALID_FORESHADOW_CATEGORIES`），让校验逻辑直接复用——保持单一事实源。

---

## 6. 建议优先级

**优先级：低**。仅校验 3（50 类）有真实价值，但收益（3 分）不抵实施风险（4 分，误杀率 + token 成本）。

理由：
- 校验 1 收益低、误杀率高 → 跳过
- 校验 2 无下游消费者 → 跳过
- 校验 3 有中等价值，但建议**改为软提示**（retry hint 提到 50 类要求，但不 reject），保留后置 fallback 兜底

**实际推荐**：把 50 类校验移到 `_build_foreshadow_catalog` 阶段，**改 fallback 策略**：从静默 fallback 到"其他"，改为在日志中**统计 + warning**，让用户从 warning 中感知"LLM 频繁输出非白名单 type"。

---

## 7. 替代方案或简化版

### 7.1 零变更版（推荐）

**不改 validate_parsed_analysis**。理由：
- 当前 4 字段校验已经覆盖"格式正确性"
- LLM 输出漂移问题（真值字符串、类型错误）已经被 P2 修复
- 再加严校验的边际收益 < 误杀成本

**评估**：收益 0 分，难度 0 分，风险 0 分。**这是最务实的方案**。

### 7.2 仅做校验 3 的"软化"版

在 `validate_parsed_analysis` 中加：
- `foreshadowing[].type` 必须在 `VALID_FORESHADOW_CATEGORIES` 内
- **不通过时**：不 reject，而是在 retry hint 中追加"type 字段必须从 50 类中选一个"
- **通过 retry 1 次仍不通过**：accept（不无限重试）

实现：
```python
def validate_parsed_analysis(data: dict) -> tuple:
    # ... 现有 4 字段校验 ...
    # 新增：50 类软校验
    for f in data.get("foreshadowing", []):
        if isinstance(f, dict) and f.get("type") and f["type"] not in VALID_FORESHADOW_CATEGORIES:
            return False, f"foreshadowing.type '{f['type']}' 不在 50 类内"
    return True, ""
```

但 LLM 第二次输出仍可能不通过 → 触发第 3、4 次重试 = token 翻 4x。**与零变更版相比，token 成本上升但收益仍有限**。

**评估**：收益 2 分，难度 2 分，风险 3 分。**比零变更版差**。

### 7.3 后置检查版（最推荐）

不改 `validate_parsed_analysis`。在 `memory_state.add_result` 内（已持 KB 锁）增加：
- 检查 `core_events.characters` 拆分后是否在 KB 已知角色集合中
- 检查 `foreshadowing[].type` 是否在 50 类内
- 检查 `cross_block.contextual_link` 是否含 `< chapter_number` 的数字

**失败行为**：仅 logger.warning，不 reject。聚合阶段 `_build_foreshadow_catalog` 已经做归一化，最终报告不受影响。

**评估**：收益 2 分（提供可观察性），难度 1 分，风险 1 分。**这是最优解**。

---

## 8. 实施路径

### 方案 A：零变更（推荐）

不做任何代码改动。在 AGENTS.md 注明：
- `validate_parsed_analysis` 仅做"格式正确性"校验
- 跨章一致性是**统计可观察性**问题，不是**拒绝问题**
- 50 类校验在 `_build_foreshadow_catalog` 阶段兜底

### 方案 B：仅加 50 类硬校验（如果用户坚持）

1. 在 `backend/config/constants.py` 新增 `VALID_FORESHADOW_CATEGORIES = frozenset(name for name, _, _ in FORESHADOW_CATEGORY_DEFS)`
2. 在 `validate_parsed_analysis`（analyzer.py:25-46）末尾追加：
   ```python
   VALID = {name for name, _, _ in FORESHADOW_CATEGORY_DEFS}
   for f in data.get("foreshadowing", []):
       if isinstance(f, dict):
           t = f.get("type", "")
           if t and t not in VALID:
               return False, f"foreshadowing.type '{t}' 不在 50 类内（必须从 {len(VALID)} 类中选一个）"
   ```
3. `build_retry_messages`（analyzer.py:136）自动把错误拼进 retry hint，无需额外改动
4. 扩 `test_models.py`：
   - 通过：type="身份"
   - 不通过：type="自定义伏笔"
   - 通过：type=""（空 type 不校验）
   - 通过：foreshadowing=[]（无对象）
5. CHANGELOG 增加"50 类硬校验"条目

### 方案 C：后置软校验（次推荐）

1. 在 `memory_state.add_result` 内（持 `_kb_lock` 后）追加：
   ```python
   # 软校验：仅 warning，不 reject
   VALID_CAT = {name for name, _, _ in FORESHADOW_CATEGORY_DEFS}
   for f in result.foreshadowing:
       if f.type and f.type not in VALID_CAT:
           logger.warning(f"第{result.chapter_number}章伏笔 type='{f.type}' 不在 50 类内")
   ```
2. 不影响 LLM 重试链，零 token 成本
3. 提供一个聚合统计：在 final_summary 完成后输出"全书中 X 条伏笔 type 不在 50 类内"

**预计工时**：
- 方案 A：0 小时
- 方案 B：2-3 小时（含测试）
- 方案 C：1-2 小时
