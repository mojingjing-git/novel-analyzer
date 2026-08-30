# 审核报告 #12：最终报告质量评分

> 范围：final_summary_report.md 一次生成无质量反馈 → 加轻量 LLM-as-judge pass 评分
> 调研时间：2026-08-27
> 数据来源：`backend/services/final_summary.py` · `backend/services/summary_service.py` · `backend/core/llm_client.py` · `backend/config/settings.py` · `frontend/src/pages/SummaryPage.vue`

---

## 1. 源码调研摘要

### 1.1 现状：单次生成、无质量反馈
- **报告生成路径**：
  - `FinalSummaryRunner.run()`（`final_summary.py:1494-1744`）执行 4 阶段 → 返回 `final_report` 字符串。
  - `summary_service.py:163-165` 写入 `output_dir / "final_summary_report.md"`：
    ```python
    report_path = output_dir / "final_summary_report.md"
    await asyncio.to_thread(report_path.write_text, report, encoding="utf-8")
    ```
  - **没有"质量检查"环节**——报告生成完就写盘。
- **报告 prompt**（`FINAL_SYSTEM_PROMPT` `final_summary.py:149-198`）：
  - 8 节固定结构：故事主线 / 角色弧光 / 伏笔网络 / 世界观演变 / 主题思想 / 节奏曲线 / 质量评价 / 写作风格分析。
  - **§7 "质量评价"** 已经要求 LLM 自评（1-10 分），但**这是 LLM 的一次性自评，不是独立 judge**。
- **报告消费**：
  - 前端 `SummaryPage.vue:395-396` 直接渲染 `v-html="reportHtml"`。
  - 没有任何"分数/质量标签"展示。

### 1.2 关键发现
1. **报告生成是 L4 阶段 4 的最后一步**（`final_summary.py:1708-1729`），由 `_call_llm_final` 单次调用完成，**max_tokens=130000**（来自 `self.config.api.max_tokens`）。
2. **没有 retry/regenerate 机制**——`success` 为 False 时直接抛 `RuntimeError("最终汇总LLM调用失败")`（`final_summary.py:1729`）。
3. **`DEFAULT_FINAL_REPORT_MIN_WORDS=5000`**（`constants.py:25`）—— 5 千字是字数下限，**不是质量分**。
4. **`MAX_OUTPUT_TOKENS=130000`**（`constants.py:50`）—— 远超实际需要（5000 字 ≈ 7K tokens）。
5. **LLM 自我评价（§7）不可信**——LLM 倾向给"8-9 分"美化自己的输出（**LLM 自评偏差**）。
6. **报告质量真正的问题**：
   - 字数不达标（< 5000 字）——可以**自动检测**
   - 8 节缺漏（LLM 漏写 §4 或 §6）——可以**自动检测**
   - 引用章节号不准确（**最难自动化**）
   - 节奏曲线/主题思想**内容空洞**（最难判定）

### 1.3 与其它模块的耦合
- **`_call_llm_final`**（`final_summary.py:628-647`）：
  - 独立 LLM 调用，与阶段 1/2/3 共用 `self._llm_sem`（并发控制）。
  - `validate_response=None`——**不验证**。
  - 可以独立加 judge pass。
- **`_emit_progress`**（`final_summary.py:564-570`）：进度事件。**可以加 "judge" 阶段**。
- **`summary_service.py:163-165`**：报告写盘后**无后续动作**——**可以加 "judge 写分" 步骤**。
- **`FINAL_USER_TEMPLATE`**（`final_summary.py:201-212`）：不涉及。
- **前端**：
  - `SummaryPage.vue`：直接渲染 reportHtml——**可以在 report 上方加"质量分"卡片**。
  - 但当前没有现成的"质量分" UI 组件。
- **断点续跑**：
  - 报告生成**不参与 checkpoint**（只卷摘要+reconciliation 有 checkpoint）。
  - **加 judge pass 不影响 checkpoint**。

### 1.4 已有覆盖
- **`DEFAULT_FINAL_REPORT_MIN_WORDS=5000`**（`constants.py:25`）—— **字数下限**是已有的"质量底线"。
- **§7 "质量评价" 自评 1-10 分**（`final_summary.py:189-192`）—— **LLM 自评**已存在。
- **批量重试机制**（`llm_client.py` 的 `temperature_max_retries + backoff_max_retries`）—— 但**只重试 LLM 调用本身**，不重试"质量不达标"。

### 1.5 风险点
- **LLM-as-judge 自身有偏差**（倾向给高分，**比 LLM 自评更可信但仍有偏差**）。
- **judge pass 增加 LLM 调用次数** = 增加 token 费。
- **judge pass 增加时间** = 报告生成时间 ×1.5-2 倍。
- **judge 评分与 LLM 自评不一致时**——**以谁为准**？需要决策。
- **judge 不能修复报告**——只能给分，**用户还得手动重跑**。

---

## 2. 多角度评分

| 维度 | 评分（1-5） | 说明 |
|---|---|---|
| 改进难度 | ⭐⭐ (低) | 加 1 个 LLM judge 调用 + 评分写入 + 前端展示；不破坏现有流程 |
| 改进收益 | ⭐⭐⭐ (中) | 给用户"报告质量"信号，但**无法直接修复低质量报告**——本质是"信号"不是"修复" |
| 代码复杂度提升 | ⭐⭐ (低) | 主要加 1 个 `_judge_report_quality` 方法 + 1 个 progress 事件 + 前端 1 个组件；约 150-200 行 |
| 维护难度 | ⭐⭐ (低) | 改动局部，集中在 final_summary.py |
| 兼容性风险 | ⭐ (低) | 不改 schema，不影响 checkpoint，不影响老书 |
| 测试覆盖成本 | ⭐⭐⭐ (中) | 至少 4-5 个测试：judge 解析容错、评分与报告绑定、低分告警、不阻塞写盘 |
| 实施风险 | ⭐⭐ (低) | 唯一风险是 judge pass 失败时不影响主流程——**必须用 try/except 包裹** |

**总评**：⭐⭐⭐ 投入小、风险小、收益中。**值得做，但价值天花板有限**（本质是给"质量信号"，不能直接修复）。

---

## 3. 关键发现

### 3.1 "质量评分"的价值天花板
- **真正的高价值**是"质量差时自动重生成"——但这会**指数级增加 token 费**（每次重生成 ×5 倍 L4 时间）。
- **次高价值**是"质量差时给低分 + 提示用户重跑"——这是**可接受的成本**（1 次 judge 调用 ≈ 5-10K tokens）。
- **低价值**是"加个 judge pass 但什么都不做"——只是给用户看个分数。

**建议**：实现"次高价值"——judge 给分，**低分时**在 progress 里告警，**让用户决定是否重跑**。

### 3.2 LLM-as-judge 的偏差问题
- LLM 倾向给 7-9 分（**天花板效应**）。
- 实际分布可能是 6-9 分——**很难出现 < 6 的低分**。
- **建议**：用"5 维分项评分"（连贯性/深度/准确性/覆盖度/可读性），每维 0-10，**比单一总分更稳定**。

### 3.3 自动化质量检测 vs LLM judge
**不需要 LLM 就能检测的**：
- 字数（> 5000 字）✓
- 8 节是否齐全（用正则匹配 `## \d+\. `）✓
- 章节号引用次数（至少 10 个）✓
- 是否包含"无法分析"等兜底词 ✓

**需要 LLM judge 的**：
- 章节号引用是否准确（**最关键但最难**）
- 主题思想是否有深度
- 伏笔判断是否前后一致
- 节奏曲线是否合理

**建议**：**先做程序化检测**（低成本 + 70% 价值），**后做 LLM judge**（高成本 + 30% 价值）。

### 3.4 与"LLM 自评"的关系
- §7 自评分数（`final_summary.py:189-192`）和 judge 分数**应该**做对比。
- **如果自评 8 但 judge 6 → 提示用户复核**（"自评与 judge 差异 > 1.5 分"）。
- **如果两者一致（自评 7 / judge 7）→ 高置信**。

### 3.5 judge pass 的实现成本
- **1 次 LLM 调用**，输入 = 报告全文 + 评分 prompt（~500 字），输出 = 5 维分数 + 评语（~500 tokens）。
- 成本：~10K tokens（M3 模型）≈ 几分钱。
- 时间：~5-15 秒。
- **token 费增加 ~5%**（L4 报告总成本约 200-500K tokens）。

### 3.6 judge pass 失败时的处理
- judge pass **不能阻塞**报告生成主流程。
- judge 失败 → 报告仍写盘，但 quality_score = None。
- **必须用 try/except 包裹**。
- 进度事件 `{"type": "judge_failed", "reason": "..."}` 让前端知道"评分失败但报告正常"。

---

## 4. 实施风险

### 4.1 schema 兼容性
✅ **不涉及 schema 改动**——quality_score 只是报告元数据，可以存到 `output_dir / "final_summary_quality.json"` 独立文件。

### 4.2 老书影响
- 13 本老书的 `final_summary_report.md` 没有 quality_score。
- **不需要 backfill**。
- **可选**：在用户查看老书报告时，提示"该报告未评分"。

### 4.3 prompt 体积膨胀
- judge prompt 极小（~500 字 + 报告全文 5000-20000 字）。
- 报告全文在 L4 阶段 4 已经生成，**复用**即可，不需要再次传给 LLM。
- **实际 prompt 体积 ≈ 25K 字符**（报告 + judge 模板）。**不撑爆 32K 模型窗口**。

### 4.4 50000 tokens 输出上限
- judge 输出 ~500 tokens（5 维分数 + 评语）。
- **不撞墙**。

### 4.5 L4 报告生成时间增加
- 当前 L4 总时间：30-60 分钟/本（1500 章书）。
- judge pass：5-15 秒。
- **时间增加 < 1%**。

### 4.6 与现有"5 阶段流程"的耦合
- 当前是 4 阶段：分卷→复检→风格→报告。
- 加 judge 后变 5 阶段：分卷→复检→风格→报告→评分。
- 前端 LaneView 进度条需要加第 5 个 phase。
- **`final_summary.py:1708-1729` 报告生成** → 加 `_judge_report_quality(final_report)` → 写 `final_summary_quality.json`。
- **不影响 checkpoint**（checkpoint 只覆盖阶段 1 卷摘要 + reconciliation）。

### 4.7 judge pass 的"可重跑性"
- 用户看完报告后想看评分 → 当前实现需要重跑 L4 才能评分——**太贵**。
- **建议**：judge pass 做成**独立 API**（`POST /api/summary/judge?book_id=xxx`）——可以随时触发，不需要重跑整个 L4。
- 这样用户能"先看报告，再决定是否评分"——**灵活性更高**。

---

## 5. 兼容性影响

### 5.1 断点续跑
- ✅ **不影响**。报告生成 + judge pass 都不参与 checkpoint。

### 5.2 老书结果
- 13 本老书的 `final_summary_report.md` 不含 quality_score。
- **不需要 backfill**。
- 用户可以**手动触发 judge API** 给老书报告评分——**对老书友好**。

### 5.3 前端
- `SummaryPage.vue`：**可以加**"质量评分"卡片（顶部或右侧），显示 5 维分项 + 总分。
- **如果 judge pass 集成到 L4 主流程**，前端在 `complete` 事件里读 quality_score。
- **如果 judge 做成独立 API**，前端加"给报告评分"按钮，**按需触发**。
- **推荐方案**：先集成到 L4（简单），后续做独立 API（灵活）。

### 5.4 Excel 导出
- 不影响（Excel 导 L1 聚合数据）。

### 5.5 SettingsPage
- 可选：加 `enable_report_quality_judge: bool`（默认 True 启用 judge pass）。
- False 时跳过 judge，节省 token。

### 5.6 13 本老书的具体处理
- **不需要 backfill**。
- 用户可以选择**只跑 L4 judge pass**（如果做成独立 API）。

---

## 6. 建议优先级

**P2（中）**

理由：
- 投入小、风险小、收益中等。
- 价值天花板有限——**给信号但不能修复**。
- **可以分阶段做**：先做程序化质量检测（字数/节数/章节号引用），再做 LLM judge。
- **真正的 ROI**在"低分告警 + 触发用户重跑"而不是"加个分数显示"。

---

## 7. 替代方案或简化版

### 方案 A（推荐先做）：纯程序化质量检测（无 LLM）
- 字数检查（> 5000）→ 通过/不通过
- 8 节齐全检查（正则匹配 `## \d+\. `）→ 通过/不通过
- 章节号引用次数（至少 10 个）→ 通过/不通过
- 兜底词检测（"无法分析"/"暂无"/"请人工"）→ 通过/不通过
- **不调用 LLM**，纯规则。
- **0 token 成本**，**0 额外时间**。
- **写入 `final_summary_quality.json`**：`{checks: {word_count: {value: 5500, pass: true, threshold: 5000}, ...}, overall_pass: true/false}`
- **前端展示**：4 个检查项的通过/不通过。
- **改动量**：~100-150 行。

### 方案 B：程序化检测 + LLM judge（完整版）
- 在方案 A 基础上加 1 次 LLM judge 调用。
- judge 评分：5 维分项（连贯性/深度/准确性/覆盖度/可读性），每维 0-10。
- **token 成本 +5%**。
- **改动量**：~250-350 行。

### 方案 C：LLM judge + 自动重生成（高成本）
- 方案 B + 如果 judge 总分 < 6 → 自动重生成报告。
- **token 成本 ×2-3 倍**。
- **真正的高价值**——但**可能烧太多钱**。
- **不建议**——除非用户强烈要求"自动重生成"。

### 方案 D：独立 API（最灵活）
- `POST /api/summary/judge?book_id=xxx`。
- 用户**按需触发**。
- 不阻塞 L4 主流程。
- **改动量**：~300 行（API + 前端 + 后端 judge 逻辑）。

---

## 8. 实施路径

如果走 **方案 A**（推荐先做），步骤如下：

1. **在 `final_summary.py` 加 `_compute_quality_metrics(final_report: str) -> dict`**：
   - 字数检查
   - 8 节齐全检查（用正则 `r"##\s*\d+\.\s*\S+"`）
   - 章节号引用次数（`r"第\d+章"` 计数）
   - 兜底词检测（"无法分析"/"暂无数据" 等）
2. **在 `run()` 末尾**（`final_summary.py:1740-1743` 之前）调用 `_compute_quality_metrics`：
   - 写入 `output_dir / "final_summary_quality.json"`
   - emit progress：`{"type": "quality", "metrics": {...}}`
3. **在 `summary_service.py:163-165` 报告写盘后**，**多写一个文件**：
   - `final_summary_quality.json`
4. **前端 `SummaryPage.vue`**：
   - 在报告上方加"质量检查"卡片（4 个 ✓/✗ 图标 + 数值）
   - 当 `overall_pass=false` 时显示"⚠️ 建议重跑"提示
5. **单元测试**：
   - 字数检查边界
   - 8 节齐全边界（缺 1 节、错位等）
   - 章节号引用次数边界
   - 兜底词检测边界
6. **跑 3 本老书**验证（用现有的 final_summary_report.md，**不需要重跑 L4**）。

**预估工作量**：~100-150 行代码 + ~80 行测试。

**关键收益**：
- 0 token 成本、0 额外时间。
- 立即给用户"报告质量底线"信号。
- 可以**重跑老书**（不需要 L4 重新跑）——纯程序化检查。

**后续路径**（方案 B/D）：
- 用户接受度验证后再加 LLM judge。
- 优先做"独立 API"（方案 D），让用户按需触发。
- 不集成到 L4 主流程——避免增加 L4 时间。
