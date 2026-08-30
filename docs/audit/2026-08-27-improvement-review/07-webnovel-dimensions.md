# 审核报告 #7：网文专项分析维度（golden_finger / power_level / face_slapping / reader_hook / filler_score）

> 范围：单章 AnalysisResult schema 新增 5 个网文专项字段的可行性审核
> 调研时间：2026-08-27
> 数据来源：`backend/core/prompt_builder.py` · `backend/models/analysis_result.py` · `backend/utils/aggregate_utils.py` · `backend/utils/excel_export.py` · `backend/utils/character_card_generator.py` · `frontend/src/pages/CharacterCardPage.vue` · `frontend/src/pages/SummaryPage.vue`

---

## 1. 源码调研摘要

### 1.1 现状
- **SYSTEM_PROMPT**（`prompt_builder.py:11-79`）角色定位为通用「小说结构分析师」，**不是**网文专项。9 类输出 schema：`core_events / character_arcs / foreshadowing / plot_holes / locations / spatial_relationships / cross_block / updated_knowledge / long_context_insights`。
- **AnalysisResult**（`analysis_result.py:95-110`）已 9 个顶层 dataclass 字段，且**所有字段都是 100% 向后兼容**——`from_dict` 用 `data.get('xxx', default)` + dataclass 默认值，旧 result.json 无新字段时一律空列表/空串/默认值落地（`analysis_result.py:206-216, 277-278`）。
- **Prompt 体积**（`prompt_builder.py:206-215`）：SYSTEM 区 = `SYSTEM_PROMPT` (~70 行) + 50 类伏笔表（每类 name+description，平均 ~20 字，**约 1000 字符**）+ `[已知世界观]` + `[主题元素]`。User 区 9 块独立组装，最大块是 `[当前分析文本]` 上限 `max_content_chars=30000` 字。
- **50 类伏笔表**（`constants.py:80-144`）已经**为网文做了大量铺垫**——题材专用类有 10 个：穿越、系统、升级、商战、职场、推理、科幻设定、奇幻设定、医疗、其他。冲突维度的"战斗/危机/对抗/伤亡/复仇"也高度网文适配。情节维度的"爽点（装逼、打脸、扬名、立威）"更是直接对位 `face_slapping`。

### 1.2 关键发现
1. **Schema 加字段是侵入最小的一类改动**。`AnalysisResult.from_dict` 已经为 `locations / spatial_relationships`（上一轮新增）做了范例——`get('xxx', [])` + dataclass 默认值，老 result.json 无新字段时不会出错，只是新字段为空。
2. **50 类伏笔表已经把"爽点/打脸/装逼"作为合法伏笔 type**（`constants.py:101`）。这意味着 `face_slapping` 可以**作为伏笔 type 的一种**来记录，而不是在 analysis_result 顶层新建字段——前提是 LLM 能在 prompt 约束下稳定识别"打脸场景"作为伏笔线索。
3. **SYSTEM_PROMPT 已经接近 70 行**。再加 5 维（即使每维 2 行）会进一步挤压上下文窗口，但**远未到撑爆**的程度——`SYSTEM` 区在 32K 模型里通常只占 5-8K tokens，还有 24K+ 给 user 段。
4. **LLM 输出 50000 tokens 上限**（`MAX_OUTPUT_TOKENS=130000` 实际，但 9 类输出在 130K 内还远）。新增 5 个字段如果每章 5-10 个实例，单章输出增量约 200-500 tokens，**基本无撞墙风险**。

### 1.3 与其它模块的耦合
- **`aggregate_utils.py`**：目前 9 类输出**各自一个聚合方法**（`aggregate_core_events / character_arcs / foreshadowing / plot_holes / ...`）。如果新增 5 维，要么 a) 不聚合（只对单章有意义），要么 b) 加 5 个 `aggregate_*` 方法+5 个 `_aggregated.json` 文件+5 个 Excel Sheet。
- **`excel_export.py:75-100`**：每个聚合文件对应一个 Sheet（核心事件、人物弧光、伏笔…）。新字段如果要导出，**需要新增 Sheet**，但 Excel 用户能否消化"5 个新 Sheet"是个 UX 问题。
- **`character_card_generator.py`**：只消费 `character_tracking_aggregated.json`（= `core_events` + `character_arcs` 派生）。**新字段不会被角色卡使用**——这是它的"隔离区"。
- **前端消费**：
  - `CharacterCardPage.vue`：消费的是 `core_events + character_arcs`，**新字段不影响**角色卡 UI。
  - `SummaryPage.vue`：直接渲染 `final_summary_report.md`（L4 阶段 4 报告），**新字段不直接影响**——除非 L4 prompt 也消费新字段。
  - `StatsPage.vue`：消费 `aggregate_long_context_insights` 派生数据，**新字段不影响**（除非再做派生）。
- **聚合文件大小**：单章 result.json 当前 5-10 KB（9 类输出 + raw_response 约 5KB），加 5 维约增 500 字节-1KB，对 `novel_analysis_aggregated.json` 全书 1500 章量级增加 0.5-1MB。**可接受**。

### 1.4 已有覆盖
- **foreshadowing 内的"爽点"** type 已经是 50 类之一（`constants.py:101`）——`face_slapping` 可以直接用伏笔维度表达。
- **foreshadowing 的 `importance` 字段**（`analysis_result.py:38`）已经区分"高/中/低"——`reader_hook`（钩子强度）可以用 importance 的高/中/低隐式表达。
- **locations**（地图数据）和 **character_arcs.change_delta**（人物变化幅度）已经在新版本里提供部分"网文"信息——比如 change_delta 可反映 `power_level` 升级幅度。

---

## 2. 多角度评分

| 维度 | 评分（1-5） | 说明 |
|---|---|---|
| 改进难度 | ⭐⭐ (低) | schema 字段新增是低侵入，但 prompt 加 5 维需要 LLM 在已经 9 维基础上稳定输出 |
| 改进收益 | ⭐⭐⭐ (中) | 命中网文用户的核心分析需求；但前 4 本书（《大王饶命》《盗墓笔记》《天官赐福》等）已经分析过，retrofit 收益不可见 |
| 代码复杂度提升 | ⭐⭐ (低) | 主要是 prompt 文字、5 个 dataclass 字段、5 个聚合方法。代码量增加约 200-300 行 |
| 维护难度 | ⭐⭐⭐ (中) | 每加 1 维都要同时改：SYSTEM_PROMPT、AnalysisResult、from_dict 兼容、聚合方法、Excel Sheet、可能的前端页 |
| 兼容性风险 | ⭐⭐ (低) | `from_dict` 已经示范了"新字段缺失→默认值"模式，老 result.json 不需要 backfill 也能加载（只是新字段空） |
| 测试覆盖成本 | ⭐⭐⭐ (中) | 至少需要：schema 兼容性测试、prompt 解析测试、5 维 LLM 输出解析容错测试、聚合测试。约 8-10 个新单元测试 |
| 实施风险 | ⭐⭐ (低-中) | 最大风险是 LLM 不能稳定输出这 5 维（尤其 `filler_score` 这种主观判断），需要 prompt 充分举例 |

**总评**：⭐⭐ 简易高收益、长期可演进，但有"维度膨胀"风险——5 维一起加会冲淡单维质量。

---

## 3. 关键发现

### 3.1 "5 维一起加"是过度设计
单章分析 prompt 已经 9 类输出。LLM 在单次分析里同时输出 14 类字段，**注意力会分散**——尤其 `filler_score`（注水率）这种主观维度，LLM 容易在缺乏原文证据时输出空串或编造。建议：

- **第一阶段只加 1-2 维**（建议 `power_level` + `face_slapping`），跑 3-5 本书验证 LLM 输出质量。
- **第二阶段再考虑 `reader_hook / filler_score`**，且 `filler_score` 应该是数值字段（0-1）而不是字符串。

### 3.2 已有覆盖足以代替"新建维度"
- `face_slapping` → 已有伏笔 `type="爽点"` + 50 类表锚点"装逼、打脸、扬名"。新增独立字段**重复定义**。
- `power_level` → 可在 `character_arcs.change_delta` 隐式表达（"筑基 → 金丹"的 delta），或在 `core_events.function` 里说"实力跃升"。
- `reader_hook` → 伏笔的 `importance="高"` 已经是高强度钩子；`long_context_insights.pacing` 已经隐式给出"紧张/舒缓"。

### 3.3 `filler_score` 难以稳定输出
**注水率**是综合性判断（事件密度、对话量、信息增量），LLM 单章分析时**没有全书上下文**，很难输出稳定的注水率分数。如果一定要做，建议**改成 L4 阶段 4 在最终报告里打分**，而不是 L1 单章分析里。

### 3.4 网文 vs 通用小说的定位
用户画像是"分析 13 本网文，不做创作辅助"。SYSTEM_PROMPT 当前是**通用分析师**——新增 5 维后**只对网文样本有效**（玄幻/都市/科幻），对其他类型（严肃文学、推理小说）会产生大量空字段。**兼容方案**：
- 设置一个开关 `enable_webnovel_dims: bool`（`AnalysisConfig` 新字段，默认 False 保持向后兼容）。
- 或者在 SYSTEM_PROMPT 末尾加一句条件式："如果本书不是网文，golden_finger/power_level/face_slapping/reader_hook/filler_score 输出空数组 `[]`"。

### 3.5 50000 tokens 输出上限不构成障碍
`MAX_OUTPUT_TOKENS=130000`（`constants.py:50`）但实际单章输出受 LLM 实际窗口限制。即便按 9 类最坏情况（每章 3-5 个 core_events、5 个 character_arcs、3 个伏笔、5 个主题、2 个长上下文洞察等），JSON 大约 1500-2500 tokens。**加 5 维、每维 1-2 个实例，额外约 200-500 tokens**。撞墙风险极低。

---

## 4. 实施风险

### 4.1 schema 兼容性（旧书结果如何处理）
✅ **安全**——`AnalysisResult.from_dict`（`analysis_result.py:151-284`）已经为 `locations / spatial_relationships` 做了范例：用 `data.get('locations', [])` + 默认值。13 本老书**不需要 backfill**，重新加载时新字段为空即可。

但是**反方向**有问题——如果用 LLM 重新分析了某本书，result.json 会有新字段；但**只读了老 result.json 的其他分析任务**（如 L4 最终报告的伏笔总表构建）需要确保不抛 KeyError。✅ `AnalysisResult.from_dict` 的所有字段都从 `data.get(..., default)` 拿，安全。

### 4.2 prompt 体积膨胀（PROMPT 已经很长了）
- 当前 SYSTEM 区 = `SYSTEM_PROMPT` (~70 行 / ~1500 字) + 50 类伏笔表（~1000 字）+ `[已知世界观]` + `[主题元素]` ≈ **3-4K 字符**。
- 加 5 维、每维 ~50 字定义 ≈ +250 字符 → 总 4.5K 字符。**约 +7%**。可接受。
- **风险点**：LLM 在 prompt 末尾的字段定义往往输出不稳定。`filler_score` 涉及主观判断，**建议放在 prompt 顶部**而不是末尾。

### 4.3 50000 tokens 输出上限
不构成风险，详见 §3.5。

### 4.4 字段语义重叠
- `face_slapping` 与 50 类伏笔的 "爽点" 重复 → **建议砍掉独立字段**，用伏笔 type 表达。
- `power_level` 与 `character_arcs.change_delta` 重复 → **建议作为 character_arcs 的子字段**，或保留独立字段但加 prompt 强约束"与 change_delta 互补"。
- `golden_finger`（金手指）→ 50 类伏笔的"系统"已经覆盖（`constants.py:135`）→ 同样建议**用伏笔 type 表达**而不是新字段。

### 4.5 LLM 误用字段的风险
新增 5 维**没有 ground truth**——LLM 不会真知道"主角当前是筑基还是金丹"。它会**自己造数字**。如果用户对网文知识有强预期，LLM 编造的金手指等级会**误导用户**。**建议**：在 prompt 里明确"如果章节未提及金手指等级，输出 `未知` 而不是估算"。

---

## 5. 兼容性影响

### 5.1 断点续跑（checkpoint）
- 13 本老书的 result.json 不含新字段 → 断点恢复时反序列化为默认值。✅ **无影响**。
- `Location`/`SpatialRel` 的前例（`analysis_result.py:206-216`）已经验证过兼容性。

### 5.2 老书结果（要不要 backfill）
**不需要 backfill**。但需要决定：
- **是否在重新分析时强制重跑**？建议**否**——保留原 result.json，新增字段空即可。
- **如果用户想要新字段**？需要"重新分析"按钮触发 L1 重跑（已经在 UI 里有"重跑单章"功能）。✅

### 5.3 前端
- `CharacterCardPage.vue`：**无影响**（消费 character_tracking_aggregated.json）。
- `SummaryPage.vue`：**无影响**（直接渲染 final_summary_report.md）。
- `StatsPage.vue`：**无影响**（消费 long_context_insights 派生数据）。
- 如果要给前端新增"网文维度"展示页（独立页或 StatsPage 子 Tab），需要：
  - 5 个新的 `_aggregated.json` 文件读取 API
  - 1 个新页面/新 Tab（前端 ~200-300 行 Vue）

### 5.4 Excel 导出（`excel_export.py`）
- 需要新增 5 个 Sheet（每个新字段 1 个）。
- 但**5 个 Sheet 信息密度低**（每章只 1-3 行），可能让 Excel 变得笨重。**建议**：
  - 合并为 1 个 Sheet"网文维度"（每行 1 章 1 实例）。
  - 或用条件式：设置 `enable_webnovel_dims=True` 时才导出。

### 5.5 SettingsPage
- `enable_webnovel_dims: bool`（新字段，默认 False 保持向后兼容）。
- UI 加一个开关："启用网文专项分析维度（仅对网文有效，通用小说会输出空字段）"。
- 需要在 `AnalysisConfig` 加字段、`_coerce_fields` 强转、`SettingsPage.vue` 加 UI、`client.ts` DTO 加字段。

### 5.6 13 本老书的具体处理
**不需要 backfill**——通过 `from_dict` 默认值处理。如果用户想看新字段，必须重新跑单章 L1 分析（已经支持）。

---

## 6. 建议优先级

**P2（中低）**

理由：
- 收益**对前 13 本老书不可见**（除非重跑 L1）。
- 与已有伏笔 50 类表**有大量功能重叠**。
- 5 维一起加是过度设计，建议**分阶段**。
- 真正的高价值维度是 `filler_score`（用户拆书向），但**应该放在 L4 最终报告里**做（因为需要全书对比），而不是 L1 单章。

如果**只做 1 维**做 POC，建议选 **`filler_score`（注水率）作为 L4 最终报告的指标**，而不是 L1 schema 改动——ROI 更高、改动更小、复用已有 L4 流程。

---

## 7. 替代方案或简化版

### 方案 A（推荐）：砍掉 L1 schema 改动，把"网文维度"放在 L4 报告里
- **保留 9 类 L1 输出不变**。
- 在 `FINAL_SYSTEM_PROMPT`（`final_summary.py:149-198`）加一个 §9 "网文专项分析"小节，要求 LLM 在最终报告里输出：
  - 金手指类型与等级变化
  - 主要打脸/装逼事件
  - 钩子密度节奏
  - 注水率评估（量化 1-10 分）
- **改动量**：prompt 加 1 节 + `_format_style_for_prompt` 类似的格式化器 + Excel Sheet × 1。
- **好处**：不需要 L1 改 schema，不需要 13 本老书重跑，不需要担心 prompt 体积膨胀。
- **坏处**：L1 阶段看不到"打脸事件"的细粒度分类（只伏笔 type 表达）。

### 方案 B（折中）：L1 只加 `golden_finger` + `power_level` 2 维
- 用 prompt 强约束："如果章节未提及金手指/等级，输出 `未知`"。
- **改动量**：schema 2 字段、聚合 2 方法、Excel 2 Sheet。
- 跑 3-5 本书验证 LLM 输出稳定性后，再决定是否加 `face_slapping / reader_hook / filler_score`。

### 方案 C（不推荐）：5 维一次性全加
- 风险大，LLM 注意力分散，单元测试覆盖成本高。
- 与 50 类伏笔表功能重叠多。

---

## 8. 实施路径

如果决定走 **方案 B**（L1 加 2 维），推荐实施步骤：

1. **设计 dataclass**（`analysis_result.py` 新增）
   - `GoldenFinger`：`type` (系统/空间/重生/其他)、`description`、`evolution` (等级变化)、`first_seen_chapter`。
   - `PowerLevel`：`character`、`from_level`、`to_level`、`change_reason`、`chapter`。
2. **加 schema 字段**：`AnalysisResult` 加 `golden_finger: List[GoldenFinger]` + `power_level: List[PowerLevel]`，`to_dict/from_dict` 兼容旧 JSON。
3. **改 SYSTEM_PROMPT**（`prompt_builder.py:30-50`）：在分析规则 §10-§11 加 2 个新维度的输出要求。
4. **加聚合方法**（`aggregate_utils.py`）：`aggregate_golden_finger` + `aggregate_power_level`，各对应 `_aggregated.json` 文件。
5. **加 Excel 导出**（`excel_export.py:75-100` 后）：2 个新 Sheet。
6. **可选：设置开关** `AnalysisConfig.enable_webnovel_dims`，默认 False 保持向后兼容。
7. **单元测试**：
   - 老 result.json 不含新字段 → 加载成功、新字段为空
   - 新 result.json 含新字段 → 正确反序列化
   - 聚合测试
   - prompt 解析容错（LLM 偶尔输出字符串而非数组的容错）
8. **跑 3 本老书**验证 LLM 输出稳定性（建议《大王饶命》《天官赐福》《盗墓笔记》）。
9. **用户验收**后再考虑扩到 5 维或上 L4 方案 A。

**预估工作量**：~600-800 行代码 + ~150 行测试 + 3 本书人工验证（每本 5-10 章样本）。
