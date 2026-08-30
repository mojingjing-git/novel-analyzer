# 审核报告 #9：语义卷边界

> 范围：final_summary.py 按 `summary_batch_size=30` 固定切卷 → 改用 `paradigm_layers` + `global_milestones` 检测自然弧线边界
> 调研时间：2026-08-27
> 数据来源：`backend/services/final_summary.py` · `backend/core/pipeline.py` · `backend/models/knowledge.py` · `backend/core/prompt_builder.py` · `backend/config/constants.py` · `backend/config/settings.py`

---

## 1. 源码调研摘要

### 1.1 现状：固定 30 章一切
- **`FinalSummaryRunner.run()`**（`final_summary.py:1494-1744`）按 `self.batch_size`（默认 30，可在设置中改，**min 5** 见 `queue_service.py:726` `max(5, config.analysis.summary_batch_size)`）做**纯机械切分**：

```python
# final_summary.py:1534-1539（关键代码）
for i in range(0, total_chapters, self.batch_size):
    batch = results[i:i + self.batch_size]
    batch_idx = len(batch_tasks) + 1
    ch_start = batch[0]['ch']
    ch_end = batch[-1]['ch']
```

- 用户已经能改 `summary_batch_size`（`settings.py:187`），但**改的是大小，不是边界**。
- 切出的每一卷做：① LLM 卷摘要（`BATCH_SYSTEM_PROMPT`）② 伏笔 reconciliation（`RECONCILIATION_SYSTEM_PROMPT`）→ 写 checkpoint。

### 1.2 关键发现：可用的"自然弧线"信号
- **`rolling_structured.paradigm_layers`**（`pipeline.py:54-100`）：
  - 范式 = "世界观/力量体系重大切换" 时新增条目（`is_active=true`，旧的 `is_active=false`）。
  - 真实**卷切换点**在 paradigm_layers 的 `chapters` 字段（如 `"100-200"`）和新旧 layer 的边界。
  - **典型场景**：玄幻网文的"炼气期 → 筑基期 → 金丹期"，每一期都是天然卷。
- **`rolling_structured.global_milestones`**（`pipeline.py:54-90`）：
  - "每条 ≤30 字，概括一个阶段的核心剧情转折，最多 5 条"。
  - 格式 `"ch1-30: 一句话概括该段剧情"`——**章节范围字段**天然就是卷边界信号。
  - 触发条件是"归档"（`pipeline.py:960-984` `_archive_momentum_to_milestone`）——当 recent_momentum 跨越 `ROLLING_MOMENTUM_WINDOW=50` 章或 `ROLLING_ARCHIVE_TRIGGER_COUNT=10` 条时**自动生成**。
- **`KnowledgeBase.rolling_structured`**（`knowledge.py:31`）：已经持久化到 `rolling_summary.json`。
- **触发时机**：`ROLLING_EARLY_CHAPTERS=100`（`constants.py:41`）首次生成；之后每章 `pipeline._update_structured_rolling()` 更新（`pipeline.py:850-925`）。

### 1.3 与其它模块的耦合
- **`final_summary.py`**：所有 4 阶段（分卷→复检→风格→报告）都依赖 batch_tasks 列表。**改边界意味着重构 4 个阶段的所有 `_run_batch` / `_recheck_remaining` / `_write_report` 调用点**。
- **断点续跑**（`final_summary.py:1093-1217`）：
  - checkpoint 用 `batch_size + results_fingerprint` 做失效判断。
  - 改边界后 → checkpoint 全部失效（因为 `batch_size` 不变但 batch_tasks 重新计算后的 ch_start/ch_end 会变）。
  - **意味着**用户改 batch_size 或切边界逻辑时，旧的 `volume_*.md / recon_*.json / manifest.json` 全部作废，已有的卷摘要要重跑。
- **卷摘要压缩**（`final_summary.py:1067-1091` `_compress_volume_summaries`）：
  - 阈值 `volume_compress_threshold=80000` 字符（`constants.py:28`）。
  - 组大小 `volume_compress_group=5`。
  - **动态卷边界** → 卷数从"30 章一卷"变到"不固定"，可能让卷数变多/变少，组大小计算需要重新评估。
- **`final_min_words=5000`**（`constants.py:25`）：最终报告的最低字数要求。**与卷数无关**。
- **`batch_summary_min_words=2000`**（`constants.py:24`）：每卷的最低字数。**与卷边界无关**。
- **`foreshadow_recheck_batch_size=40`**（`constants.py:32`）：全书伏笔复检每批伏笔数，**与卷边界无关**。

### 1.4 已有覆盖
- `global_milestones` 已经有章节范围字段（`"ch1-30: ..."`）——可以直接解析为"卷边界在 ch30 末"。
- `paradigm_layers.chapters` 已经有"100-200"格式 —— 可以直接解析为卷边界。
- 范式切换的判定由 LLM 自主完成（`pipeline.py:86`）——已经在用。

### 1.5 风险点
- **`global_milestones` 是 FIFO 淘汰的**（`pipeline.py:894-908` `max_milestones=20`，锁定首条+每范式首条），所以**早期里程碑会保留**——但中段可能掉。如果用户查 1500 章书的中段（500-800 章），只剩 ~5-10 条里程碑，**信息密度低**。
- **范式切换只在大世界观变化时触发**（`pipeline.py:67-86` "如果世界观/力量体系有重大切换才新增条目"）——对于**没有明显范式切换的网文**（如《盗墓笔记》单一盗墓世界观），`paradigm_layers` 可能只有 1 条，**完全没法切边界**。
- **`global_milestones` 的生成是惰性的**（每 `ROLLING_MOMENTUM_WINDOW=50` 章才触发）——千章书**只有 20-30 条里程碑**，间距 50 章左右，**远大于 30 章切卷的密度**。如果用户的"自然弧线"判断要细到每 30-50 章，里程碑是不够密的。
- **`paradigm_layers` 上限 `ROLLING_MAX_PARADIGMS=5`**（`pipeline.py:865`）——超过 5 个范式会被截断，**多范式长书会丢历史范式**。

---

## 2. 多角度评分

| 维度 | 评分（1-5） | 说明 |
|---|---|---|
| 改进难度 | ⭐⭐⭐⭐ (高) | 需要解析 paradigm_layers + global_milestones 的章节范围字符串、重构 4 阶段切分逻辑、checkpoint 失效处理、卷摘要压缩重算 |
| 改进收益 | ⭐⭐ (低-中) | 30 章机械切分在大部分网文里**已经能反映卷结构**；改成语义切分对单范式小说（《盗墓笔记》《天官赐福》这种）几乎无收益 |
| 代码复杂度提升 | ⭐⭐⭐⭐ (高) | final_summary.py 增加 100-200 行的"切分算法"代码（解析 paradigm/milestone、回退机制、混合切分策略） |
| 维护难度 | ⭐⭐⭐ (中) | 切分逻辑要兼顾"有范式/无范式"两种情况，单元测试覆盖复杂 |
| 兼容性风险 | ⭐⭐⭐⭐ (高) | checkpoint 全部失效，老书重跑卷摘要（**最贵阶段**）。13 本老书全要重跑 L4 |
| 测试覆盖成本 | ⭐⭐⭐⭐ (高) | 切分算法需要：① 纯范式切 ② 纯里程碑切 ③ 混合切 ④ 回退到固定 batch ⑤ 边界处理（首尾卷、不在范围内的章） |
| 实施风险 | ⭐⭐⭐⭐ (高) | 切分错了 → 报告逻辑断裂，**比"切得不准"更糟糕** |

**总评**：⭐⭐ 投入大、收益小、风险高。**不建议优先做**。

---

## 3. 关键发现

### 3.1 现有 `summary_batch_size=30` 是有意的工程折中
- **够小**：单卷 LLM 上下文不超 30 章 × ~3000 字/章 = 90K 字符 = ~30K tokens。在 32K 模型窗口安全区。
- **够大**：每卷的"主线推进/角色发展"是连续的，不会切到中间。
- **可调**：用户能改 `summary_batch_size`（min 5）。如果某本书需要更细的卷，**改设置就行**——不需要改算法。

### 3.2 语义切分不会显著提升报告质量
**对比 30 章机械切 vs 范式+里程碑切**：
- 30 章切 → 用户的最终报告会有 **50-60 个分卷**（1500 章 / 30 章），每卷 2000 字 ≈ **10-12 万字**——**已经足够细**。
- 范式+里程碑切 → 5 个范式 + 20 个里程碑 ≈ 25 个"事件锚点" → **25-30 个分卷**——**反而更粗**。

**更细的卷≠更好的报告**。30 章切是 LLM 上下文窗口和报告信息密度的**最优折中点**。

### 3.3 语义切分会让 checkpoint 失效
当前 checkpoint 校验（`final_summary.py:1175-1188`）：
```python
if m.get("batch_size") != self.batch_size or m.get("results_fingerprint") != current_fp:
    # 清空旧 checkpoint 重新跑
```

切分算法变了 → `batch_tasks` 重新计算 → 老的 `volume_*.md` 文件**chapter 范围不匹配** → 全部 checkpoint 失效。**意味着**：
- 升级此功能后，**所有老书的卷摘要必须重跑**（最贵 L4 阶段，平均 30-60 分钟/本）。
- 13 本老书全量回灌 = **数小时 token 费 + 时间**。

### 3.4 范式/里程碑的"章节范围字符串"是不稳定的
- `global_milestones` 格式如 `"ch1-30: 一句话"`（`pipeline.py:66`）—— LLM 输出时**不一定严格遵守**格式。可能是：
  - `"ch1-30: ..."`（标准）
  - `"1-30章: ..."`（LLM 自由发挥）
  - `"ch1 - ch30: ..."`（带空格）
  - `"第1-30章: ..."`（中文数字）
- 需要**鲁棒的正则解析**。如果解析失败 → 回退到固定切。

### 3.5 与"全自动" vs "半自动" 的设计哲学冲突
当前是**全自动固定切分**：用户改 batch_size 即可。

改成"语义切分"有两条路：
- **A. 全自动**（LLM 判定）：用户无感，但**切错风险高**。
- **B. 半自动**（先列切分建议，用户确认/调整）：交互成本高，**L4 阶段 4 报告是后台批跑**，不适合人工介入。

---

## 4. 实施风险

### 4.1 schema 兼容性
✅ 现有 schema 不需要改。**只改切分算法**。

### 4.2 prompt 体积膨胀
不涉及。**不调用 LLM 做"切分判定"**（因为 LLM 切分成本高于价值），只程序解析已有 `paradigm_layers` / `global_milestones`。

### 4.3 50000 tokens 输出上限
不涉及。

### 4.4 checkpoint 失效（**最大风险**）
**所有老书的 checkpoint 必须清空**。13 本老书需要全量重跑 L4 阶段 1（卷摘要，平均 30-60 分钟/本）。**总成本估计**：
- 13 本书 × 平均 45 分钟 = **9.75 小时**
- 按 M3 模型价格估算 ≈ 几百元 token 费

### 4.5 切分错误的连锁反应
- 切得太细 → 卷数 ×2，token 费 ×2
- 切得太粗 → 卷摘要"主线推进"过于笼统
- 切在不该切的地方 → 伏笔 reconciliation 漏判，**污染账本状态**

### 4.6 范式/里程碑缺失时的回退
**降级路径**：
1. `paradigm_layers` 空 → 全部用 `global_milestones` 切
2. 两者都空 → 回退到固定 `summary_batch_size`

代码需要 3 路分支 + 各自的边界处理 + 单元测试。

### 4.7 范式/里程碑格式不规范
LLM 输出的 `paradigm_layers.chapters` 可能是 `"100-200"`、`"100章-200章"`、`"ch100-ch200"` 等多种格式。**鲁棒解析** + **回退到固定切**。

---

## 5. 兼容性影响

### 5.1 断点续跑
- ❌ **现有 checkpoint 全部失效**（参见 §4.4）。
- 需要在升级时**自动清空** `final_summary_checkpoint/` 目录（`_wipe_checkpoint_dir` 已有，`final_summary.py:1118-1124`）。

### 5.2 老书结果
- 不需要 backfill `result.json`（L1 输出未改）。
- 13 本老书的 `final_summary_report.md` 仍然有效（旧报告不重生成）。
- 但**用户主动重跑 L4 时**，会用新切分逻辑，全量重生成。

### 5.3 前端
- `SummaryPage.vue`：**无影响**（直接渲染 `final_summary_report.md`）。
- 不需要改前端。

### 5.4 Excel 导出
- 不影响 Excel（Excel 消费 L1 聚合数据，与 L4 卷切分无关）。

### 5.5 SettingsPage
- 保留 `summary_batch_size` 作为"回退值"（无 paradigm/milestone 时用）。
- 可选：加 `semantic_volume_boundary_enabled: bool`（默认 False 保持向后兼容）。

### 5.6 13 本老书的具体处理
- **不需要 backfill**。
- 用户主动重跑 L4 时，自动用新切分逻辑（需要清空 checkpoint 目录）。

---

## 6. 建议优先级

**P3（低）—— 不建议近期做**

理由：
- 投入产出比极低：30 章机械切在大部分网文里**已经是合理粒度**。
- 风险大：checkpoint 全量失效，13 本老书要重跑。
- **更应该做的**：先把"卷摘要的内容质量"（比如每卷主线推进的深度）做厚，而不是切分边界。

如果坚持要做，建议 **A/B 测试**：
- **对照组**：30 章切（当前）
- **实验组**：范式+里程碑切
- **指标**：人工评价 5 本书的"报告是否更连贯"+"伏笔判断是否更准"

---

## 7. 替代方案或简化版

### 方案 A（推荐不做）：维持现状 + 加"卷边界建议"提示
- 在 L4 阶段 1 启动时，**程序侧**解析 `paradigm_layers + global_milestones`，**打印**切分建议（如"建议在 ch80 / ch200 / ch450 处切分"）到日志。
- **不实际改变切分逻辑**。用户可以**手动改 `summary_batch_size`** 来接近建议的切分。
- **代码量**：~50 行解析 + 日志输出。
- **零风险**。

### 方案 B：混合切分——固定 batch + 范式边界
- 基础切分仍是 `summary_batch_size=30`。
- 但每卷的"卷标题"显示**最近的范式/里程碑事件**（如"第 1-30 章：故事开端 · 主线：主角下山"）。
- 在卷摘要的"主线推进"段首注入 paradigm 提示。
- **改动量**：~100 行。
- **好处**：用户在最终报告里看到"卷边界"是有意义的，但不破坏切分稳定性。
- **风险**：仍需 prompt 加 1-2 句引导。

### 方案 C（不推荐）：全自动语义切
- 解析 `paradigm_layers` 的 `chapters` 字段做切分点。
- **风险**：单范式小说切不出来，回退到固定切。
- 详见 §3.2-3.4。

---

## 8. 实施路径

如果坚持走 **方案 B**（混合切分），推荐步骤：

1. **在 L4 阶段 1 之前**（`final_summary.py:1534` 之前）加 `_compute_volume_hints()`：
   - 读 `rolling_structured.paradigm_layers` + `global_milestones`。
   - 解析出"切分提示点"列表（如 `[(80, '范式切换：进入修真期'), (200, '里程碑：主角突破')]`）。
2. **在 BATCH_USER_TEMPLATE**（`final_summary.py:94-103`）加 `volume_hint` 变量：
   - 在 prompt 顶部插入"本卷特殊事件提示"（如"本卷末附近发生范式切换"）。
3. **在卷摘要输出的"主线推进"小节**——通过 prompt 强约束"在卷末时说明范式切换"（如果适用）。
4. **回退机制**：如果 `paradigm_layers` 和 `global_milestones` 都为空，`volume_hint=""` 不影响 prompt。
5. **单元测试**：
   - 解析 4 种格式的 `chapters` 字段
   - 解析 4 种格式的 `global_milestones` 章节范围
   - 空数据时回退
6. **不需要清空 checkpoint**。
7. **不需要 13 本老书重跑**。

**预估工作量**：~150-200 行代码 + ~50 行测试。

如果走 **方案 A**（仅打印建议），工作量更小（~50 行代码），但收益也很小（用户得自己改设置）。
