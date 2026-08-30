# 审核报告 #3：滚动总结频率自适应

> **结论先行：不建议按当前方案实施**。原方案把"减少 LLM 调用"的优化目标，与"用程序侧规则判断内容变化量"这两个独立课题强行耦合，会引入高复杂度和多种漏触发风险，且收益相对成本不显著。
>
> 详见 §6「建议优先级」与 §7「替代方案或简化版」。

---

## 1. 源码调研摘要

### 1.1 当前触发阈值的精确数字

| 维度 | 当前实现 | 数值/位置 |
|---|---|---|
| **首生成阈值** | `self._rolling_early` | 默认 `ROLLING_EARLY_CHAPTERS=100`，**已按书长自适应**：`min(cfg, max(30, len(all_chapters) // 3))`（`pipeline.py:267-268`） |
| **增量更新频率** | `_rolling_batch_size = concurrency` | `pipeline.py:420`，**固定每 N 块（默认 2 块）触发一次**，未做内容自适应 |
| **流式触发点** | `_completed_since_rolling >= _rolling_batch_size` | `pipeline.py:496-502`，每块完成后 `+=1`，达到 `concurrency` 触发一次 |
| **后台同步** | `asyncio.Event _rolling_done` | `pipeline.py:417-419`，串行执行 rolling（不并发） |
| **归档阈值（momentum）** | `ROLLING_ARCHIVE_TRIGGER_COUNT=10`（条目数）或 `ROLLING_MOMENTUM_WINDOW=50`（章号跨度） | `pipeline.py:871-880`，**这是已经存在的自适应归档机制**，但是发生在每次 rolling 调用内的 5b 步骤，不是触发阶段的过滤 |
| **补跑后兜底** | `_safe_post_retry_rolling_update` | `pipeline.py:940-958`，**补跑成功后强制再跑一次 rolling**，与"跳过"机制存在根本冲突 |
| **首次跨过 ROLLING_EARLY 边界** | `if not has_structured and max_chapter >= self._rolling_early` | `pipeline.py:815-817`，**不可跳过**（必须首次生成） |

### 1.2 `rolling_structured` 字段结构和大小

**Schema**（`models/knowledge.py:31` + `pipeline.py:113-119`）：

```python
{
  "global_milestones": list[str],      # ≤20 条，里程碑（FIFO淘汰，锁定的除外）
  "paradigm_layers":   list[dict],     # ≤5 条，范式层（含 is_active/chapters/core_rules）
  "current_context":   dict,           # {location, current_goal, immediate_threat}
  "active_causal_chains": list[str],   # ≤5 条，活跃因果链
  "recent_momentum":   list[str],      # ≤15 条，近期势头（ch{N}: 事件）
}
```

**尺寸估算**（典型）：
- 20 里程碑 × 50 字 ≈ 1000 字
- 5 范式 × 80 字 ≈ 400 字
- 5 因果链 × 40 字 ≈ 200 字
- 15 势头 × 30 字 ≈ 450 字
- 上下文 100 字
- **合计 ≈ 2150 字**（中文字符），JSON 包装后约 3-4 KB

→ **这是一个轻量结构**，每次 rolling 调用输入的"当前数据"部分（system prompt 中的 `STRUCTURED_ROLLING_SYSTEM_PROMPT` 模板 `{current_data}`）约 3-4K token，输出相同量级。

### 1.3 已有变化检测在哪里

| 已有机制 | 位置 | 性质 |
|---|---|---|
| **首生成阈值按书长自适应** | `pipeline.py:267-268` | 已经在用了（QUA-2） |
| **Momentum 条目数触发归档** | `pipeline.py:871-873` | 后置（LLM 调用后的程序处理） |
| **Momentum 跨度触发归档** | `pipeline.py:875-880` | 后置（同上） |
| **global_milestones FIFO 锁定** | `_find_locked_milestones`（`pipeline.py:155-179`） | 锁定 0 号 + 每个范式首条 |
| **补跑后强制 rolling** | `_safe_post_retry_rolling_update`（`pipeline.py:940-958`） | 兜底，不可跳过 |
| **首生成强制** | `pipeline.py:815-817` | 不可跳过 |
| **范式层新增** | 仅由 LLM 决策，**无程序侧检测** | 提示词要求"如果世界观/力量体系有重大切换才新增条目"（`pipeline.py:67, 86`） |

→ **关键事实**：**没有"按内容变化量跳过触发"的机制存在**。所有"自适应"都是 LLM 调用内部的后处理。

### 1.4 跳过机制如何与跨块因果链兼容

**核心冲突**：

1. **rolling 的本质是"LLM 合并"**：`_update_rolling_summary`（`pipeline.py:825-835`）向 LLM 同时喂【当前数据】+【新章节信息】，LLM 输出的是 *合并后* 的全量 JSON。如果跳过调用，新章节信息就**永远不会被合并进 rolling_structured**：
   - `recent_momentum` 不会被刷新（prompt 第 5 条要求 LLM 提取最近 10 个关键事件）
   - `current_context`（位置/目标/威胁）不会更新
   - `active_causal_chains` 不会刷新
   - `global_milestones` 不会追加
   - `paradigm_layers` 不会识别新范式

2. **跳过 ≠ 节省开销**：跳过 N 次后，下一次触发时，新章节批量可能扩大到 N×`concurrency` 块，prompt 输入翻 N 倍：
   - 当前 prompt 输入 ≈ 4K（当前数据）+ N×1.5K（新章节）= 4K + 2N K
   - 跳 5 次后，单次输入 ≈ 14K（仍远低于 32K 窗口，不爆炸但 token 费线性增长）
   - 跳 10 次后 ≈ 24K，开始逼近窗口上限
   - **结论：跳过 1-2 次没问题，跳过 5+ 次会反向放大单次成本**

3. **补跑路径不可跳过**：`pipeline.py:545-554` 在失败块补跑成功后强制再跑一次 rolling。如果主路径允许"按内容变化量跳过"，补跑后必须**覆盖**前面的跳过决定——这意味着"跳过"决策不能是终态，必须可被补跑和 paradigm shift 推翻。

---

## 2. 多角度评分

| 维度 | 评分（1=极低，5=极高） | 依据 |
|---|---|---|
| **改进难度** | 3 | 需新增 1 个 `_should_skip_rolling()` 函数 + 调用点修改 + 配置项 + paradigm shift 检测器。中等改造面 |
| **改进收益（节省 LLM 调用）** | 2 | 默认每 2 块触发一次。1000 章书约 500 次 rolling 调用，假设 30% 触发可跳 → 省 150 次 × ~10-30s ≈ 节省 25-75 分钟 + ~$1-3 token 费。相对于全书 1-3 小时的 LLM 分析占比小 |
| **代码复杂度提升** | 4 | 引入"内容变化量评估" = 在 streaming worker 中实时统计新 result 的 `core_events`/`foreshadowing`/`character_arcs` 数量 + 阈值比较 + paradigm shift 检测。横切多个数据源 |
| **维护难度** | 4 | 新增 3 个阈值参数需调优；首次/补跑/legacy 降级都要有特例；paradigm shift 检测有 FN/FP 两难；测试需覆盖每条路径 |
| **兼容性风险** | 3 | `rolling_structured` 是新主链路核心字段（`memory_state.py:_apply_rolling_to_snapshot`，P1 修复刚加）；改触发逻辑不能动数据流，但要保证多次跳过后的合并语义不变 |
| **测试覆盖成本** | 4 | 至少 6 个新测试点：跳过触发 / 不跳过触发 / paradigm shift 强制 / 补跑路径 / 续跑路径 / 边界条件（恰好达到阈值、跨窗口） |
| **实施风险** | 4 | 漏触发 = `recent_momentum`/`current_context` 永久落后；阈值过激 = 失去"主线概要"的实时性；bug 难复现（必须构造特定章节序列） |
| **综合 ROI** | **2（低）** | 实施复杂度高 + 收益相对全链路小 + 风险点多 |

---

## 3. 关键发现

### 发现 1：原方案混淆了两个独立优化目标
- **目标 A**：减少 LLM 调用（性能/成本）
- **目标 B**：让主线概要更"准"地反映内容变化（质量）

原方案"变化量小就跳过"看似同时实现 A+B，但**两者并非正相关**：
- 变化量小 → LLM 输出的 JSON 几乎不变 → 浪费调用 → **A 合理**
- 变化量小 → `recent_momentum` 不需要刷新 → 跳过也合理 → **B 看似合理**
- 但**变化量小 ≠ `recent_momentum` 不需要刷新**：`recent_momentum` 是"近 10 个关键事件"，新章节即使变化小，也可能是高密度短事件的延续型叙事（如连续战斗场面），跳过会让 momentum 滞后 5-10 章才进 KB

### 发现 2：所谓"paradigm shift 检测"在程序侧**没有可靠信号源**
- 候选信号：`updated_knowledge.world_building` 新增元素数量
- 但"新元素"和"新范式"无强对应：
  - 修真文主角筑基期进入金丹期，world_building 增长 2-3 条（小境界/新法诀/新势力），但 paradigm_layers 不需要新增（仍是同一体系）
  - 修真 → 都市的穿越文，world_building 一次性增长 5+ 条（新地名/新势力/新法则），但 paradigm shift 取决于叙事密度而非条目数
- **结论**：程序侧只能做粗筛（"world_building 单批新增 ≥ N 条"），无法替代 LLM 的语义判断，FP/FN 都会引发问题

### 发现 3：已有"按书长自适应首生成阈值"（QUA-2）证明作者已意识到此问题
- `pipeline.py:264-268` 的 `_rolling_early` 自适应就是**这个方向的简化版**：把"何时生成首次"按书长拉低到 30 章下限
- 但作者**没有把它推广到增量更新频率**，因为增量更新的本质是"持续刷新主线"，不是"达到某点才生成"
- 任何"按内容跳过增量"的尝试都把增量更新退化为"事件驱动"模式，与当前"每 N 块必触发"的可靠性契约不一致

### 发现 4：跳过逻辑的边界条件矩阵（6+ 种）
1. **首次生成**（`has_structured=False`）→ **不能跳**
2. **旧格式降级**（`_legacy_text`）→ **不能跳**（`pipeline.py:820-823`）
3. **补跑后**（`retried_block_ids`）→ **不能跳**（`pipeline.py:545-554`）
4. **跨 paradigm layer 切换** → 必须触发（语义要求）
5. **跨 momentum 归档窗口**（章号差 ≥50）→ 仍可走 LLM，但归档逻辑内嵌在 5b 步骤
6. **断点续跑**（`_flushed_chapters` 已存在）→ 当前逻辑用 `rolling_last_chapter = max(_flushed_chapters)` 兜底

→ 即原方案在 6 种边界里只能对**主线 1 种**（"正常流式增量 + 内容变化小"）生效，其余 5 种必须保留强制触发。**实际适用范围窄**。

### 发现 5：测试覆盖的隐性成本
- `tests/test_pipeline_rolling_guard.py` 已存在，专门测 rolling 的容错行为
- 新增"跳过逻辑"需要 6 个新测试 + 2 个 mock 改造点（`state.results[bid].core_events` / `foreshadowing_network` / `character_arcs`）
- 当前 `_async_rolling` 在 `asyncio.create_task` 中后台运行（`pipeline.py:500`），**测试需要 mock LLMClient 行为 + 等待 asyncio.Event**，现有测试代码已显示这一调试路径复杂
- **预计新增测试代码 200-400 行**

---

## 4. 实施风险

### 4.1 paradigm shift 检测的可靠性

| 风险 | 后果 | 缓解难度 |
|---|---|---|
| **FP 过多**（误判为 paradigm shift）→ 频繁强制触发 | 优化目标 A 失效 | 难（需调阈值） |
| **FN**（真 paradigm shift 被判定为"变化小"）→ 不强制触发 | 全书主线概要对范式切换的响应滞后 5-20 章 | 极难（信号源弱） |
| **信号源不稳健**：LLM 自身的 world_building 输出格式松散（参见 `tests/test_pipeline_p2_batch.py:37-44` 测过 `dict` 条目混入） | 数量统计本身就不准 | 需先做归一化 |
| **检测器成为新单点故障**：原方案假设检测器是纯函数，但它依赖 LLM 上一次的输出 | 跨断点/迁移/旧版数据时检测失效 | 需独立设计 |

### 4.2 跳过触发是否会丢失重要的 momentum 归档

**会**。具体路径：

1. 第 1-50 章快速推进，主线丰富，每批 `recent_momentum` 都正常追加
2. 第 51-80 章进入"低潮"（如主角闭关修炼），按新规则**跳过 15 次** rolling
3. 第 81 章出现重大事件（突破/出关）
4. 此时：
   - `recent_momentum` 仍停在第 50 章附近的势头（因为跳过的批次 LLM 没被调用）
   - `momentum_window` 跨度（首条 ch50 ~ 末条 ch81=31 章）**未达到 50**，不会自动归档
   - LLM 在 81 章这次调用里必须**同时**处理 30 章的新信息 + 合并到旧 momentum → 输出质量下降
5. **永久损失**：第 51-80 章的势头条目**永远不会进 KB**（除非后续补一次跨这 30 章的回填）

### 4.3 与 global_milestones FIFO 淘汰的交互

`global_milestones` 的 FIFO 淘汰在 `_find_locked_milestones`（`pipeline.py:155-179`）控制：

- 跳过 5+ 次后，最新 rolling 的 `current_data` 段落已经包含的 milestone 不变
- 但 LLM 在下次调用时，prompt 仍要求它"保留【当前数据】中仍有价值的条目，追加【新章节信息】中的新发现"（`pipeline.py:81`）
- 理论上 LLM 会保留所有旧 milestone → 不触发淘汰
- **实际风险低**，反而 FIFO 淘汰会更慢（侧面效应：旧 milestone 存活更久，可能稀释"近期主线"）

### 4.4 跨阈值触发的"势头"是否会滞后

**会，且不可恢复**（见 §4.2）。`recent_momentum` 是有损结构（最多 15 条），跳过的章节的势头**只在 LLM 重新见到时才能进 momentum**，而 LLM 只看到"当前 momentum + 新章节"——跳过的中间章节**对 LLM 不可见**。

---

## 5. 兼容性影响

### 5.1 对断点续跑的影响

- 当前续跑用 `state.rolling.get("last_updated_chapter", 0)` 恢复进度（`pipeline.py:405-406`）
- 跳过逻辑若依赖 `_rolling_state` 内的临时变量，需保证该变量在续跑时被正确恢复
- **风险**：`_rolling_state = {"last_chapter": rolling_last_chapter}` 是函数内 dict，**重启后丢失**（`pipeline.py:421`），但续跑时通过 `state.rolling` 重建，所以一致性问题不大
- **额外风险**：跳过决策如果跨续跑"持有"（例如"已经跳过 3 次，下次必触发"），重启后这个决策状态丢失，可能从 0 重新累计

### 5.2 对 KB 完整性的影响

- `get_kb_snapshot` 通过 `_apply_rolling_to_snapshot` 注入 `rolling_structured`（`memory_state.py:141-153`）
- `kb.rolling_structured = rs` 是**直接引用**（不是 deep copy）
- 跳过期间 `state.rolling` 不变 → 注入的快照也不变
- **但**：`kb` 的其他字段（`recent_summaries`、`compressed_arcs`）由 `_merge_one` 持续追加（`memory_state.py:328-341`），与 `rolling_structured` **不同步**
- 后果：跳过 10 次后，`compressed_arcs` 已含 10 条新 timeline，但 `recent_momentum` 仍指向 10 章前
- **下游 prompt**（`prompt_builder.py:140-147`）同时注入两者 → 主线概要与逐章记录"时间错位"
- **最终报告阶段**（4 阶段流程）会读到这种错位 → 总结报告可能出现"近期事件"与"全局里程碑"时间不一致

### 5.3 对总结阶段 4 阶段流程的影响

- 4 阶段流程（伏笔复检 → 卷摘要 → 最终报告 → 风格提取）不直接读 `rolling_structured`，读的是 `kb` 整体
- 如果跳过导致 `rolling_structured` 滞后，会通过 `prompt_builder._render_structured_rolling`（`prompt_builder.py:259-319`）影响**逐章分析的 prompt**
- **下游所有分析章节的 prompt 质量下降** → 分析结果质量下降 → 卷摘要质量下降 → 最终报告质量下降
- **级联放大效应**：跳过 30% 的 rolling 调用，可能导致全书 30% 章节的 prompt 失去最新主线概要

---

## 6. 建议优先级

| 优先级 | 项 | 备注 |
|---|---|---|
| **P0** | **不实施**完整原方案 | ROI 太低，风险太高 |
| **P1** | 实施**替代方案 A**（仅 paradigm shift 强制触发，不做跳过） | 单一维度、零漏触发风险 |
| **P2** | 实施**替代方案 B**（合并相邻"低变化"批次为单次 LLM 调用，但不跳过） | 等价于把 `concurrency` 临时放大 |
| **P3** | 长期：实施**替代方案 C**（放弃 LLM 增量更新，改用程序侧 merge） | 大重构，但能从根上消除 LLM 增量开销 |

---

## 7. 替代方案或简化版

### 方案 A：仅"范式切换强制触发"，不做跳过

**改动面**：
- 不改 `_async_rolling` 的触发频率
- 仅在 `_completed_since_rolling >= _rolling_batch_size` 判定后，**额外**检查"是否检测到 paradigm shift"（程序侧粗筛 `world_building` 新增条数）
- 若检测到 → 立即触发 rolling（不等 batch 满）
- 若未检测到 → 维持现状

**代码量**：约 30-50 行新增，0 行删除
**风险**：FP 风险（误判为 paradigm shift 提前触发）— 后果**只是**多一次 LLM 调用，**不是漏触发**
**收益**：捕获"低频重大事件"的能力提升，不优化成本

### 方案 B：动态批量合并（推荐）

**改动面**：
- 新增配置：`rolling_max_skip_threshold = 5`（最多连续跳过 5 次后必须触发）
- `pipeline.py:496` 判定改为：
  ```python
  if _completed_since_rolling >= _rolling_batch_size:
      if not _should_skip(...) or _skip_count >= rolling_max_skip_threshold:
          # 正常触发
      else:
          _skip_count += 1
          continue
  ```
- 跳过决策依据：本次新批次的 `core_events` 数量 + `foreshadowing` 数量 + `character_arcs` 变化量**都低于阈值**
- paradigm shift 信号：**不用于跳过决策**（避免 FP），仅用于**提前触发**（不增加成本）

**代码量**：约 80-120 行新增，10 行修改
**风险**：仍存在 §4.2 的 momentum 滞后，但被 `max_skip_threshold=5` 兜底
**收益**：典型长书节省 15-30% rolling 调用（具体取决于内容变化密度）

### 方案 C：放弃 LLM 增量更新（长期）

**改动面**（大）：
- `recent_momentum`：改为程序侧从 `result.cross_block.summary` 提取（regex 或简单切片）
- `current_context`：改为从 `result.updated_knowledge.timeline` 提取
- `active_causal_chains`：保留 LLM 合并，但**不每批都调**，改为每 10 批调一次
- `global_milestones`：仅在 paradigm shift 时调 LLM
- `paradigm_layers`：仅在 world_building 增长 ≥5 时调 LLM

**代码量**：约 500+ 行新增
**风险**：高（要重新设计程序侧 merge 的语义），且完全改变了 rolling 的契约
**收益**：消除 80%+ 的 LLM 增量调用，但需要重新验证主线概要质量

### 方案 D：不优化

- 当前实现**已经**做了：
  - 首生成阈值按书长自适应（QUA-2）
  - 增量调用受 `concurrency` 限流（不会失控）
  - 失败时安全降级（`_safe_post_retry_rolling_update`）
  - 续跑时正确恢复 `rolling_last_chapter`
- 多次 P0/P1 修复表明**当前实现存在隐性 bug 风险**（如 `paradigm_layers` 字符串元素的 AttributeError，见 `pipeline.py:165-168`）
- **优先稳定 > 优化**

---

## 8. 实施路径

### 如果选择方案 A（推荐 P1）

```
Phase 1（半天）：
  1. 新增 helper `_detect_paradigm_shift_signal(new_results: list) -> bool`
     - 实现：统计本次批次的 world_building 新增元素数，> 阈值返回 True
  2. 在 `pipeline.py:496` 前插入：
     if _detect_paradigm_shift_signal(state.results[bid] for bid in _rolling_pending_ids):
         # 立即触发（不等 batch 满）
         ...
  3. 新增配置 `rolling_paradigm_world_threshold: int = 3`（单批 world_building 新增 ≥3 视为疑似范式切换）
  4. 新增 1-2 个单元测试（覆盖触发 / 不触发两种路径）

Phase 2（半天）：
  1. 灰度验证：选 3-5 本已分析的书，重新跑 rolling，验证主线概要质量不退化
  2. 监控 24h 看 paradigm shift 触发率（预期 5-15% 批次会触发）
  3. 根据实际数据微调阈值
```

### 如果选择方案 B（备选 P2）

```
Phase 1（1 天）：
  1. 设计 `_should_skip_rolling(new_results) -> Tuple[bool, str]`
     - 输入：本批 new_results（从 state.results 取）
     - 输出：(是否跳过, 原因)
     - 三个条件：core_events 数量 < N1, foreshadowing 数量 < N2, character_arcs 变化量 < N3
  2. 在 `pipeline.py:496` 改写判定逻辑
  3. 新增 `_skip_count` 状态变量 + `rolling_max_skip_threshold` 配置
  4. paradigm shift 检测独立：仅用于"提前触发"，不影响跳过

Phase 2（1 天）：
  1. 6+ 单元测试（每个边界 + 正常路径）
  2. 1 个集成测试：100 章书跑完整 pipeline，对比有/无跳过的 KB 差异
  3. 灰度验证 + 阈值微调
```

### 如果选择方案 C（长期，不推荐近期）

> **不在本次审核范围**。建议作为单独的"rolling 子系统重构"项目立项，独立审计 + 设计 + 实施。

---

## 9. 附录：源码引用清单

- `backend/core/pipeline.py`
  - L50-108：`STRUCTURED_ROLLING_*` 提示词
  - L113-179：`_DEFAULT_ROLLING_SCHEMA` / `_validate_and_fill_rolling_schema` / `_extract_chapter_number` / `_find_locked_milestones`
  - L264-268：首生成阈值按书长自适应
  - L400-438：`_rolling_done` Event + `_rolling_state` dict + `_async_rolling` 闭包
  - L466-507：流式并发 + rolling 触发点
  - L519-554：循环结束 + 补跑后强制 rolling
  - L766-938：`_update_rolling_summary`（核心逻辑）
  - L940-958：`_safe_post_retry_rolling_update`
  - L960-986：`_archive_momentum_to_milestone`
  - L988-1085：`_reinitialize_structured_rolling` + `_generate_early_summary`
  - L1087-1125：`_format_chapters_for_rolling` / `_format_chapters_for_rolling_light`
- `backend/core/memory_state.py`
  - L34：`self.rolling: dict = {}`
  - L40：`_rolling_lock`
  - L141-153：`_apply_rolling_to_snapshot`（P1 修复）
  - L194-200：`flush_to_disk` 中 rolling 落盘
  - L281-289：`restore_from_disk` 中 rolling 恢复
- `backend/models/knowledge.py`
  - L31：`rolling_structured: dict`
  - L38-53：`_load_rolling_structured`（含旧格式降级）
  - L68-117：`from_dict`
- `backend/config/constants.py`
  - L34-47：所有 `ROLLING_*` 常量
- `backend/config/settings.py`
  - L177-181：`AnalysisConfig` 中 rolling_* 字段
- `backend/core/prompt_builder.py`
  - L140-147：调用 `_render_structured_rolling`
  - L259-319：`_render_structured_rolling` 实现
- `backend/tests/test_pipeline_rolling_guard.py`、`test_memory_state_rolling.py`、`test_pipeline_p2_batch.py`：现有测试，参考基线

---

**审核人**：Worker 子 agent
**日期**：2026-08-27
**版本**：v1（最终）
