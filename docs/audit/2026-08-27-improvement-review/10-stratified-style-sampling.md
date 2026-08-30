# 审核报告 #10：分层风格采样

> 范围：`style_analyzer.py` 风格采样从"前半随机 3 + 后半随机 3" → 改"按 paradigm layer 分层采样，每层 2 章"
> 调研时间：2026-08-27
> 数据来源：`backend/core/style_analyzer.py` · `backend/core/pipeline.py` · `backend/models/knowledge.py` · `backend/services/final_summary.py`

---

## 1. 源码调研摘要

### 1.1 现状：随机"前半 3 + 后半 3"采样
- **`sample_chapters`**（`style_analyzer.py:198-240`）：
  - `n=6`，前半 3 章 + 后半 3 章随机采样。
  - 每章 1500 字随机位置采样。
  - 固定种子 `random.Random(42)` 保持稳定。
  - **完全基于章节位置**，不考虑"故事阶段"。
- **`compute_book_stats`**（`style_analyzer.py:397-420`）：
  - 全部章节做硬指标统计（如句长、对话密度、词汇丰富度等）。
  - **全量统计是分层的天然基础**——但只聚合平均值，不分阶段。
- **`extract_style_profile`**（`style_analyzer.py:423-452`）：调用 `compute_book_stats` + LLM 语义提取 8 维。
- **`SEMANTIC_FIELDS`**（`style_analyzer.py:303-307`）：8 维语义字段（signature_expressions / narrative_rhythm / dialogue_style / rhetorical_preferences / emotional_expression / narrative_voice / information_control / narrator_and_genre）。

### 1.2 关键发现：可用的"分层"信号
- **`rolling_structured.paradigm_layers`**（`pipeline.py:54-100`）：
  - 已有 `chapters` 字段（如 `"1-100"`、`"100-300"`）。
  - **天然就是分层**。
  - 上限 `ROLLING_MAX_PARADIGMS=5`（`pipeline.py:865`）。
- **`global_milestones`**（`pipeline.py:66`）：也可作为分层锚点（每条里程碑带章节范围）。
- **风格特征在范式切换时可能突变**——比如《凡人修仙传》从"凡人界"到"灵界"修辞风格会变。这是分层的合理动机。
- **`analyze_chapter_stats`**（`style_analyzer.py:106-156`）已经是逐章函数 → **天然支持分层聚合**。

### 1.3 与其它模块的耦合
- **`final_summary._run_style_extraction`**（`final_summary.py:1230-1261`）：调用 `extract_style_profile` 一次，**不感知分层**。
- **`_format_style_for_prompt`**（`final_summary.py:1263-1298`）：把 `style_profile` 展平为单段文本注入最终报告 prompt。**当前是单段不分层**。
- **`FINAL_SYSTEM_PROMPT`**（`final_summary.py:149-198`）§8"写作风格分析"小节要求"基于【写作风格特征】数据，分析本书的写作特点"——**不强制要求分层**。
- **`KnowledgeBase.rolling_structured`**：在 `knowledge.json` 里持久化。**风格分析时直接读**即可。

### 1.4 已有覆盖
- `analyze_chapter_stats` 是逐章统计，**逻辑已经支持分层**——只需传入 chapter list 而不是全 chapters。
- 风格特征按 paradigm 切换——**网文的"风格变化"通常伴随范式切换**，**这是该改进的强动机**。

### 1.5 风险点
- **范式/里程碑可能稀疏**：单范式小说（如《盗墓笔记》单一盗墓世界观）只有 1 个 layer，**没法分层采样**。
- **小型长书**（如 200 章的短篇）：可能只有 2-3 个 layer，每层 2 章 = 4-6 章——**与当前 6 章相当**，**改进收益有限**。
- **超大长书**（如 1500 章的网文）：layer 上限 5 个，每层 2 章 = 10 章——**信息密度反而下降**。
- **每层 2 章 vs 6 章全量**：6 章全量覆盖更广（前后各 3），分层 2 章/层在"层内变异"上有信息损失。

---

## 2. 多角度评分

| 维度 | 评分（1-5） | 说明 |
|---|---|---|
| 改进难度 | ⭐⭐ (低) | 主要改 `sample_chapters` 的"分层逻辑"；`analyze_chapter_stats` 已经是逐章 |
| 改进收益 | ⭐⭐⭐ (中) | 对"多范式"网文（修仙、玄幻）有强价值；对"单范式"网文几乎无价值 |
| 代码复杂度提升 | ⭐⭐ (低) | 加 ~50-100 行的"分层采样算法" + 解析 paradigm.chapters 的鲁棒正则 |
| 维护难度 | ⭐⭐ (低) | 改动局部，集中在 style_analyzer.py |
| 兼容性风险 | ⭐ (低) | 不改 schema，不改聚合，不改 Excel |
| 测试覆盖成本 | ⭐⭐⭐ (中) | 需要：① 单范式回退 ② 多范式分层 ③ 边界（layer 只有 1 章） ④ 与随机种子的可复现性 |
| 实施风险 | ⭐⭐ (低) | 主要风险是 LLM 8 维语义提取在"层 2 章样本"下不稳定 |

**总评**：⭐⭐⭐ 投入小、风险小、收益中等。**值得做，但优先级不高**。

---

## 3. 关键发现

### 3.1 当前"前半 3 + 后半 3"已经覆盖了风格变化
- 前 3 章 → 故事开端的风格
- 后 3 章 → 故事中后期的风格
- **如果书的范式切换发生在 1/3 → 2/3 区间**，当前采样**会**捕获到。
- **如果范式切换发生在书的中段（如 40-60%）**，当前采样**会**漏掉（前半 3 没采到新范式，后半 3 也没采到）。

### 3.2 风格突变其实很罕见
- 大部分网文**整体风格一致**（同一作者）。
- 真正"突变"主要在：① 主角换地图 ② 视角切换 ③ 重大事件后。
- 这些**不总是和 paradigm_layer 同步**。
- **改 paradigm 分层 ≠ 一定改到"风格突变点"**。

### 3.3 "每层 2 章"样本量可能不足
- LLM 8 维语义提取需要足够上下文才能稳定输出。
- 2 章 = 3000 字上下文，**对"对话风格/修辞偏好/句式节奏"等宏观维度**勉强够用。
- 对"标志性表达/口癖"这种需要**多场景**才能识别的维度，2 章样本**太少**。
- **建议**：每层 3 章（总数 = layer 数 × 3）而不是 × 2。

### 3.4 风格特征当前已经足够
- 8 维语义风格已经覆盖"叙事节奏/对话风格/修辞偏好/情感表达"——**拆书向用户已经够用**。
- 加分层后**用户能区分"前期/后期"风格**，但**报告里如何呈现？**
  - 方案 a：每个 layer 一段 8 维描述 → **报告变长**（5 个 layer × 8 维 = 40 段）
  - 方案 b：合并为"以 X 为分界，前期为 A，后期为 B" → **仍是 1 段，但内容更多**
- **方案 b 更可读**。

### 3.5 统计部分不需要改
- `analyze_chapter_stats` 是逐章函数，**已经支持分层**——只需在聚合时按 layer 分组即可。
- 真正的"统计分层"是**聚合时按 layer 分组平均**，而不是"采样时按 layer 选章"。
- **建议**：统计全量（不采样），但分 layer 聚合，输出 `style_profile.statistical.by_layer` 而不是 `style_profile.statistical`（单值）。

### 3.6 语义部分可以加
- 采样按 layer → 每层独立 LLM 调用 → 输出 8 维特征。
- **总调用次数**：1 → N（layer 数）。**Token 费 ×N**。
- 对于 5 个 layer 的书：5 × 8 维 × 2000 tokens ≈ 10 万 tokens。**token 成本增加约 5 倍**。
- **建议**：单 LLM 调用，**prompt 里传入每个 layer 的样本**，让 LLM 输出 per-layer 特征。这样 token 费只 ×1.5（多了 layer 标签和分组说明）。

---

## 4. 实施风险

### 4.1 schema 兼容性
✅ **不涉及 schema 改动**——`style_profile` 已经有 `statistical / semantic / sample_chapters` 三个 key。新增 `by_layer` 子结构是**纯加字段**（不破坏老 key）。

### 4.2 prompt 体积膨胀
- 6 章 × 1500 字 = 9000 字 → 当前 LLM 8 维调用输入。
- 每层 2 章 × 5 layer = 10 章 × 1500 字 = 15000 字 → 增加约 60%。
- 仍在 30K-50K 字符范围，**不撑爆 32K 模型窗口**。

### 4.3 50000 tokens 输出上限
- LLM 输出 8 维语义约 1500-2000 tokens。
- **per-layer 模式**：要求 LLM 输出 5 段 × 8 维 = 40 段 ≈ 8000-10000 tokens。
- 接近上限但**不撞墙**（`MAX_OUTPUT_TOKENS=130000` 是输入限制，输出限制看厂商）。

### 4.4 LLM 在小样本下的稳定性
- 2 章样本对"标志性句式"判断**不稳定**——LLM 倾向于用常见的"标志词"（如"只见"）冒充"这本书的标志"。
- 统计部分（句长、对话密度）**稳定**——纯数据。
- **建议**：统计按 layer 分组（稳定），语义仍用全量 6 章（不按 layer 分）。

### 4.5 范式缺失时的回退
- 0 个 layer → 用当前"前半 3 + 后半 3"逻辑
- 1 个 layer → 用全量 6 章（不分层）
- 2+ 个 layer → 每层 3 章
- **回退路径要清晰**，否则 L4 报告风格段会缺数据。

---

## 5. 兼容性影响

### 5.1 断点续跑
- ✅ **不影响**。风格提取不参与断点续跑（`_run_style_extraction` 不写 checkpoint，`final_summary.py:1230-1261`）。
- 老书重跑 L4 时，**自然会用新风格逻辑**——但 LLM 仍会输出语义，只是按 layer 切分。

### 5.2 老书结果
- 13 本老书的 `style_profile` 已经是**单段不分层**。**不需要 backfill**。
- 但**报告里风格段重生成时会变化**——用户可能觉得"风格分析变了"。

### 5.3 前端
- `StylePage.vue`：**可能需要适配**新结构（如果 `by_layer` 字段在前端有消费）。
- 但当前 `style_profile` 是注入到 L4 报告 prompt 里的，**前端不直接消费**——查 `StylePage.vue` 验证。

### 5.4 Excel 导出
- 不影响。Excel 只导出 L1 聚合数据，不含风格。

### 5.5 SettingsPage
- 可选：加 `style_stratified_sampling: bool`（默认 True 启用新逻辑），False 时回退到旧"前半 3 + 后半 3"。
- 让用户能选择。

### 5.6 13 本老书的具体处理
- **不需要 backfill**。
- 重跑 L4 时，**自然会用新逻辑**。

---

## 6. 建议优先级

**P2（中）**

理由：
- 投入小（~100-200 行代码）。
- 风险小（不破坏 schema，不影响 checkpoint）。
- 收益中等（多范式网文受益，单范式无感）。
- **不影响老书**——可以随时回滚。
- **建议作为"风格分析强化"的子任务**而不是独立大改。

---

## 7. 替代方案或简化版

### 方案 A（推荐）：统计按 layer 分组，语义仍用全量
- **统计部分**：`aggregate_stats` 改 `aggregate_stats_by_layer(results, paradigm_layers)` → 输出 `statistical.by_layer = {layer_id: stats_dict}`。
- **语义部分**：仍用全量 6 章随机采样（不按 layer 切）。
- **报告呈现**：在 `_format_style_for_prompt` 末尾加一段"按范式变化的风格特征"（基于 by_layer 统计差异）。
- **改动量**：~150 行（主要在 style_analyzer 和 final_summary 之间的接口）。
- **好处**：统计稳定（数据驱动），语义稳定（样本量不变），报告增量信息（按范式对比）。
- **风险**：0。

### 方案 B：纯语义按 layer 分层
- 采样按 layer → 每层独立 LLM 调用。
- **Token 费 ×N**（5 layer = 5 倍）。
- **报告变长**（40 段风格）。
- 详见 §3.6。

### 方案 C：完全分层（统计+语义都按 layer）
- 同 B + 统计按 layer 分组。
- **最重方案**，但**最完整**。
- 改动量 ~300 行。

### 方案 D（不推荐）：保留旧逻辑，仅在 L4 报告里"按范式分组呈现"
- 不改 style_analyzer.py。
- 在 `_format_style_for_prompt` 加"按 paradigm layer 标注风格变化"段。
- **零代码风险**。

---

## 8. 实施路径

如果走 **方案 A**（推荐），步骤如下：

1. **新增 `compute_layer_aware_stats`**（`style_analyzer.py` 新增）
   - 输入：blocks_dir + paradigm_layers 列表 + milestones
   - 输出：`{"by_layer": {layer_id: stats_dict}, "overall": stats_dict}`
2. **新增 `aggregate_stats_by_layer`**（`style_analyzer.py`）
   - 用 paradigm_layers 的 `chapters` 范围切分 chapters
   - 对每层 chapters 调用 `analyze_chapter_stats` → `aggregate_stats`
3. **改 `extract_style_profile`**（`style_analyzer.py:423-452`）：
   - 接受 `paradigm_layers` 参数
   - 返回 `{"statistical": {"overall": ..., "by_layer": ...}, "semantic": ...}`
4. **改 `_run_style_extraction`**（`final_summary.py:1230-1261`）：
   - 读 `KnowledgeBase.rolling_structured.paradigm_layers`
   - 传给 `extract_style_profile`
5. **改 `_format_style_for_prompt`**（`final_summary.py:1263-1298`）：
   - 加 "按范式变化" 段（基于 `by_layer` 差异）
6. **回退机制**：
   - `paradigm_layers` 为空 → 用旧逻辑
   - 某个 layer 只有 1 章 → 跳过该 layer（聚合意义不大）
7. **单元测试**：
   - 单范式回退
   - 多范式分层聚合
   - 边界（layer 只有 1 章）
   - 与 `random.Random(42)` 的可复现性
8. **跑 3 本老书**（《大王饶命》多范式 + 《天官赐福》单范式 + 《盗墓笔记》单范式）验证。
9. **用户验收**后启用。

**预估工作量**：~200-300 行代码 + ~100 行测试 + 3 本书人工验证。

**关键收益**：
- 报告的"按范式变化的风格"段让用户能直接看到"凡人界 vs 灵界"风格差异。
- 统计精度提升（按 layer 聚合，**平均值更能反映层内一致性**）。
- LLM 语义提取成本不变（仍用 6 章全量）。
