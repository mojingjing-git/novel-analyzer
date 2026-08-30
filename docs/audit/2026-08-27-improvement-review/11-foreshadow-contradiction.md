# 审核报告 #11：伏笔矛盾检测

> 范围：foreshadow_ledger.py 状态机只跟踪 active/resolved/dormant → 增加"同伏笔多个回收事件一致性检查"
> 调研时间：2026-08-27
> 数据来源：`backend/utils/foreshadow_ledger.py` · `backend/services/final_summary.py` · `backend/core/prompt_builder.py` · `backend/config/constants.py` · `backend/utils/aggregate_utils.py`

---

## 1. 源码调研摘要

### 1.1 现状：3 态状态机
- **`ForeshadowItem`**（`foreshadow_ledger.py:31-55`）：
  - 核心字段：`id / description / first_seen_chapter / first_seen_batch / last_seen_chapter / last_seen_batch / status / resolution / resolved_chapter / resolved_batch / source_type / evidence_chapters / confidence / notes / needs_review`。
  - **`resolution` 是字符串**（`foreshadow_ledger.py:41`）——**只能存一个"回收描述"**。
  - **`resolved_chapter` 是 int**（`foreshadow_ledger.py:42`）——**只能存一个"回收章节"**。
  - **`evidence_chapters: List[int]`**（`foreshadow_ledger.py:45`）——可以存多个"证据章"（伏笔被提及/被推进的章节）。
- **`ForeshadowLedger`**（`foreshadow_ledger.py:58-189`）：
  - 只有 `reconcile_and_update`（休眠判定，`foreshadow_ledger.py:103-131`）和 `audit_text_for_report`（审计报告，`foreshadow_ledger.py:133-173`）两个核心方法。
  - **没有"伏笔间矛盾检测"机制**。
- **`ForeshadowItem.status`** 取值：`active / resolved / dormant`（`foreshadow_ledger.py:40`）。

### 1.2 关键发现：当前模型已能"识别"伏笔被多次提及，但**不识别"多次被回收"的矛盾**
- **`evidence_chapters: List[int]`**（`foreshadow_ledger.py:45`）记录"伏笔被推进/被提及的所有章节"。
- **`_apply_reconciliation`**（`final_summary.py:1300-1338`）：
  - 当 LLM 标记 `is_resolved_in_this_batch=true` 时：
    - `existing.status = 'resolved'`
    - `existing.resolution = item.get('resolution_summary')`（覆盖之前的！）
    - `existing.resolved_chapter = item.get('resolved_chapter')`（覆盖！）
  - **第二次标记为回收时，会**覆盖**第一次的 resolution 和 resolved_chapter**——**没有任何"多版本"机制**。
- **`_run_global_foreshadow_recheck`**（`final_summary.py:1376-1492`）：
  - 全书伏笔复检，**同样逻辑**——`existing.status = 'resolved'` 直接覆盖。
  - **如果第一次复检说"已回收"（ch100），第二次复检说"在 ch50 已回收"**——`resolved_chapter` 从 100 改为 50，**resolution 也被覆盖**。

### 1.3 实际场景中的"矛盾"
- **场景 A：同伏笔在两个章节被分别回收**（如 LLM 误判"伏笔 1 在 ch50 回收"+"在 ch120 回收"）——当前模型只保留**最后一次**的回收记录，**第一次的"回收"在账本里被吞掉**。
- **场景 B：同伏笔在不同卷摘要里被矛盾地标记**（如批次 1 说"未回收"，批次 2 说"已回收"）——**当前模型以批次 2 为准**，**批次 1 的"未回收"判断被静默丢弃**。
- **场景 C：LLM 在 reconciliation 阶段和 global_recheck 阶段对同一伏笔给出不同答案**——**当前模型以 recheck 为准**（因为 recheck 后跑），**但 reconciliation 的判断也是合法的**——**两者都应被记录**。
- **场景 D：伏笔被回收后又重新激活**（如"主角失忆后伏笔再次埋设"）——**当前模型只能 `resolved → active`**（手动改），**没有自动化机制**。

### 1.4 与其它模块的耦合
- **`_build_foreshadow_catalog`**（`final_summary.py:791-908`）：
  - 伏笔总表从 result.json 派生，**每个 fs_id 对应一个伏笔**。
  - 已经有 `merged_count` 字段（`final_summary.py:929`）记录"合并了几条原始伏笔"——但**没有"多个回收记录"**字段。
- **`_format_catalog_for_prompt`**（`final_summary.py:916-937`）：
  - 在 BATCH_USER_TEMPLATE 注入"截至本批已识别的伏笔清单"——`[fs_001] type|clue (ch1,15,837)`。
  - **没有"伏笔已回收过几次"的标注**。
- **`_build_active_foreshadows_block`**（`final_summary.py:945-980`）：
  - 构建"当前伏笔清单"——只显示 `status='active'` 的条目，**已 resolved 的不再出现**。
  - **已 resolved 的伏笔不进入后续批次的 reconciliation prompt**——**所以同伏笔不会再次被 recheck 标记**（除非 status 被回滚）。
- **`audit_text_for_report`**（`foreshadow_ledger.py:133-173`）：
  - 生成的伏笔审计报告只显示**单条 resolution**——**多次回收信息丢失**。
- **`50 类伏笔表`**（`constants.py:80-144`）：已经覆盖大多数类型，**矛盾检测可以基于 type + description 相似度做**。

### 1.5 已有覆盖
- `evidence_chapters: List[int]`：**已经能记录伏笔被提及的所有章节**——是"多次回收"的基础。
- `notes: List[str]`（`foreshadow_ledger.py:47`）：**已经有"备注"机制**——但当前只在休眠时自动追加（`foreshadow_ledger.py:131`），**没有人工/算法记录的"回收历史"**。
- `needs_review: bool`（`foreshadow_ledger.py:48`）：**已经有"低置信度标人工复核"**机制——**矛盾伏笔正好可以标 needs_review=true**。

### 1.6 风险点
- **同伏笔多次回收**在网文中**其实很少见**——大多数伏笔只有 1 个回收点。
- **真正常见**的是"伏笔的多次推进"（= evidence_chapters 多次出现），**不是"多次回收"**。
- **LLM 误判"已回收"** 比"多次回收"更常见——但当前 `_apply_reconciliation` 已经有 `confidence != '高'` 触发 `needs_review` 的机制（`final_summary.py:1328-1332`）——**已有覆盖**。
- **schema 改动会影响老书**——`ForeshadowItem` 加新字段需要 schema version 升级，**老书的 ledger.json 需要迁移**（已有先例：v1 → v2 加 `needs_review`，`foreshadow_ledger.py:19-20`）。

---

## 2. 多角度评分

| 维度 | 评分（1-5） | 说明 |
|---|---|---|
| 改进难度 | ⭐⭐⭐ (中) | 加字段 + 改 apply_reconciliation 逻辑 + 改 audit_text；schema 升级；老 ledger.json 迁移 |
| 改进收益 | ⭐⭐ (低) | 实际"同伏笔多次回收"的场景在网文里**很少见**；更常见的是"LLM 误判"已有 needs_review 覆盖 |
| 代码复杂度提升 | ⭐⭐⭐ (中) | ledger 多版本管理 + apply 时合并策略 + audit 报告改写；约 200-300 行 |
| 维护难度 | ⭐⭐⭐ (中) | 多版本合并策略要清晰，单元测试覆盖多分支 |
| 兼容性风险 | ⭐⭐⭐ (中) | schema v2 → v3，老 ledger.json 需要迁移（13 本老书 ledger.json） |
| 测试覆盖成本 | ⭐⭐⭐ (中) | 至少 5-8 个新单元测试：多次回收合并、recheck/reconciliation 冲突、矛盾标 review |
| 实施风险 | ⭐⭐ (低-中) | 改动集中在账本和 reconciliation 应用逻辑；不影响 L1 / L4 报告生成主流程 |

**总评**：⭐⭐ 投入中、收益低、风险中。**优先级靠后**。

---

## 3. 关键发现

### 3.1 "多次回收"在网文里是异常事件，不是常规事件
- 正常的网文伏笔：埋设 → 推进 N 次（evidence_chapters） → 回收 1 次。
- 异常情况（值得检测的）：
  - 伏笔被"回收"了，但**回收后又被"再次推进"**（= 作者失忆或故意设置）——**这是真正矛盾**。
  - 同一伏笔被 LLM **误判**为在 ch50 和 ch120 都回收——**这是 LLM 错误**。
- **异常情况 1** = `resolved → active` 转换检测。
- **异常情况 2** = `resolved_chapter` 在多次 recheck 时跳变。

### 3.2 当前模型实际是"最后写入者胜"
- `_apply_reconciliation` 第二次执行时（`final_summary.py:1319-1327`）：
  - `existing.status = 'resolved'`（已经 resolved 也是 resolved）
  - `existing.resolution = ...`（覆盖）
  - `existing.resolved_chapter = ...`（覆盖）
- **没有"如果已经是 resolved，就不要再处理"的判断**——但**实际上同一伏笔在两次 reconciliation 中被标记 resolved 不会触发 status 变化**（已经是 resolved），但 `resolution` 字段会被覆盖。
- **真正应该处理的是"active → resolved"和"resolved → active"的状态转换**——**前者已经覆盖，后者缺失**。

### 3.3 真正的"矛盾"是"resolved → active"逆向转换
- **场景**：批次 1 reconciliation 误判某伏笔为已回收 → 写入 `status='resolved'` + `resolved_chapter=50`。
- **批次 2 reconciliation**（或 global_recheck）发现"该伏笔在 ch50 没有被回收" → 应该把 `status` 改回 `active`。
- **当前实现**（`final_summary.py:1316-1333`）：**如果伏笔已经是 resolved，新的 reconciliation 不会把它改回 active**。
  - 代码中 `is_resolved` 只处理 `True` 分支，**没有 `False` 分支的"un-resolve"逻辑**。
  - 意味着**一旦被错判为 resolved，永远是 resolved**——**除非人工改 JSON**。
- **这才是真正的"矛盾检测"应该解决的问题**。

### 3.4 多次回收信息保留 = 简单加字段
- `resolution_history: List[Dict]` = `[{batch_idx, resolved_chapter, resolution_text, confidence, source}]`
- `_apply_reconciliation` 改为：往 `resolution_history` 追加而不是覆盖。
- `audit_text_for_report` 显示 `resolution_history` 全部条目 + 标"⚠️ 多次回收，建议人工复核"。
- **schema 升级 v2 → v3**。
- **老 ledger.json 迁移**：`needs_review` 已有（v2），新加 `resolution_history=[]` 默认值。

### 3.5 简化版：只加"状态逆向检测"不加"多次记录"
- 真正的 ROI 在**检测矛盾**而不是**记录历史**。
- 简化版：
  - 在 `_apply_reconciliation` 加：if `existing.status == 'resolved'` and `not is_resolved_in_this_batch` → 标 `needs_review=True`，notes 追加"批次 N：发现该伏笔实际未回收，请人工复核"。
  - 同样地，如果 `existing.status == 'resolved'` and `is_resolved_in_this_batch=True` but `resolved_chapter` 不一致 → 标 `needs_review=True`。
- **不需要 schema 升级**（用 notes + needs_review 已有字段）。
- **不需要老 ledger.json 迁移**。
- **约 50-80 行代码**。

### 3.6 真正"伏笔矛盾"是"伏笔的描述与回收的描述冲突"
- 例子：埋设时 description="林家灭门真相"，回收时 resolution="林家是被山贼杀的"——但前面某章说"林家是被官府抄家的"——**两个回收描述相互矛盾**。
- 这种"伏笔内部的描述矛盾"需要**LLM 比对 description 与 resolution + evidence_chapters**——**当前没有任何自动检测**。
- **这是真正的高价值功能**——但实现成本高（需要 LLM 调用）。

---

## 4. 实施风险

### 4.1 schema 兼容性
- **方案 A**（多次回收记录）：**需要 schema v3** + 老 ledger.json 迁移（13 本）。
- **方案 B**（只加逆向检测）：**不需要 schema 升级**，用 notes + needs_review。

### 4.2 老书影响
- **方案 A**：13 本老书 ledger.json 需要从 v2 迁移到 v3。
- **方案 B**：13 本老书**自动受益**（重跑 L4 时，新逻辑会自动检测已有 ledger 里的"已 resolved 伏笔是否在后续章节被再次推进"）。

### 4.3 checkpoint 影响
- 账本 checkpoint 路径：`output_dir / "foreshadow_ledger.json"`。
- **方案 A**：v2 → v3 迁移时需要读取老 JSON 写入新 JSON，**需要原子写**（`safe_save_json`）。
- **方案 B**：直接读老 JSON，新逻辑写新状态——**无迁移成本**。

### 4.4 L4 报告影响
- 报告里的 §3 "伏笔网络"小节（`final_summary.py:164-169`）显示伏笔状态——**新逻辑会让某些伏笔的"回收方式"显示"⚠️ 多次回收，存在矛盾"**。
- **用户看到会困惑**——需要在前端加 tooltip 解释。

### 4.5 多次 LLM 调用的成本
- 方案 C（LLM 比对 description vs resolution）：每条伏笔 1 次 LLM 调用 = 千章书 × 几十条伏笔 × 1 次调用 = **几十次额外 LLM 调用**。
- 按 M3 模型估算 ≈ 几元 token 费 + 几十秒时间。**可接受**。
- **但**：当前伏笔 reconciliation 已经调了 N 次（每批 N 次），**再叠加矛盾检测会显著增加总调用次数**。

### 4.6 当前已有覆盖
- `confidence != '高'` 触发 `needs_review`（`final_summary.py:1330`）——**已经覆盖"低置信回收"**。
- **没有覆盖**："resolved 伏笔在后续章节被再次推进"（= 状态逆向）。

---

## 5. 兼容性影响

### 5.1 断点续跑
- 账本本身**有 checkpoint 机制**（`final_summary.py:1481-1487` 每完成一批立即落盘）。
- 方案 A：账本 schema 升级时**需要清空 checkpoint 重新跑 reconciliation**——成本高。
- 方案 B：账本无 schema 变化——**不影响**。

### 5.2 老书结果
- **方案 A**：13 本老书 ledger.json 需要迁移（脚本化）。13 本 final_summary_report.md 不变（报告里的"伏笔审计"段会标"多次回收"——但老报告不重跑所以不变）。
- **方案 B**：13 本老书**自动受益**于"逆向检测"——**但只有重跑 L4 才会触发**。

### 5.3 前端
- `SummaryPage.vue`：**无影响**（直接渲染 markdown 报告）。
- `StatsPage.vue`（如有）：如果展示"needs_review 伏笔数"——**新逻辑会让数字变化**。

### 5.4 Excel 导出
- 伏笔 Sheet（`excel_export.py:99-...`）：可以加列"状态变更次数"——但 Excel 当前没有这种列。

### 5.5 SettingsPage
- 不需要改设置（无新配置项）。

### 5.6 13 本老书的具体处理
- **方案 A**：需要一次性迁移脚本（schema v2 → v3）。
- **方案 B**：不需迁移，重跑 L4 时自动应用。

---

## 6. 建议优先级

**P2（低-中）**

理由：
- 实际场景中"同伏笔多次回收"**很罕见**。
- 真正常见的是"LLM 误判"，**已有 `needs_review` 机制**覆盖。
- 真正的"伏笔描述矛盾"是高价值功能，但实现成本高（需要 LLM 比对）。
- **ROI 最高的是"方案 B"**（只加逆向检测，不加多次记录）——**约 50-80 行代码，无 schema 升级**。

如果只做 1 个功能，**做"方案 B：状态逆向检测"**——其他都是过度设计。

---

## 7. 替代方案或简化版

### 方案 A：完整多次回收记录（不推荐）
- 加 `resolution_history: List[Dict]` 字段。
- schema v2 → v3。
- 老 ledger.json 迁移。
- 改动量 ~200-300 行。
- **ROI 低**——多次回收场景稀少。

### 方案 B（推荐）：状态逆向检测 + 矛盾标 needs_review
- 不改 schema。
- 在 `_apply_reconciliation`（`final_summary.py:1316-1338`）加：
  - 如果 `existing.status == 'resolved'` 且 `is_resolved_in_this_batch=False` → `needs_review=True`，notes 追加"批次 N：发现该伏笔实际未回收，请人工复核"
  - 如果 `existing.status == 'resolved'` 且 `is_resolved_in_this_batch=True` 但 `resolved_chapter` 不一致 → `needs_review=True`
- 在 `global_recheck`（`final_summary.py:1448-1473`）同样逻辑。
- **改动量 ~50-80 行**。
- **不破坏任何东西**。

### 方案 C：LLM 比对 description vs resolution（高价值高成本）
- 对每条已 resolved 的伏笔，调 LLM 比对 description + evidence_chapters vs resolution。
- 检测"伏笔描述与回收描述是否矛盾"。
- **改动量 ~300-500 行**（需要异步 LLM 调用 + 并发控制 + 与现有 global_recheck 协调）。
- **token 成本增加 ~30%**。
- **真正的高价值功能**——但优先级低于 L1/L4 核心流程改进。

### 方案 D：组合方案 B + C
- B 先做（低成本，覆盖 80% 场景）。
- C 后续做（高成本，覆盖剩余 20%）。

---

## 8. 实施路径

如果走 **方案 B**（推荐），步骤如下：

1. **在 `_apply_reconciliation`**（`final_summary.py:1316-1338`）加逆向检测逻辑：
   ```python
   if existing and existing.status == 'resolved':
       if not is_resolved:
           # 状态逆向：之前已 resolved，本批次说未回收
           existing.needs_review = True
           existing.notes.append(f"批次{batch_idx}：发现该伏笔实际未在第{ch_end}章前回收，请人工复核（原回收：ch{existing.resolved_chapter}）")
       elif is_resolved and existing.resolved_chapter and item.get('resolved_chapter') and existing.resolved_chapter != item['resolved_chapter']:
           # 回收章节冲突
           existing.needs_review = True
           existing.notes.append(f"批次{batch_idx}：回收章节冲突 ch{existing.resolved_chapter} vs ch{item['resolved_chapter']}，请人工复核")
   ```
2. **同样逻辑在 `global_recheck` 阶段**（`final_summary.py:1452-1473`）应用。
3. **改 `audit_text_for_report`**（`foreshadow_ledger.py:133-173`）：
   - 在已回收伏笔段，对 `needs_review=True` 的条目加 `⚠️ **状态异常**：` 后面接 notes 列表
4. **加单元测试**：
   - 状态逆向（resolved → active 检测）
   - 回收章节冲突检测
   - 低置信 + 逆向 同时触发 needs_review
5. **跑 2 本老书**（《大王饶命》《盗墓笔记》）验证。

**预估工作量**：~50-80 行代码 + ~50 行测试 + 2 本书验证。

**关键收益**：
- 自动捕获"LLM 误判 + 矛盾"——比 `confidence` 字段更直接。
- 不破坏 schema，不影响老书，不需要迁移。
- 用户在最终报告里看到 `⚠️ 状态异常` 提示，知道有伏笔需要人工核对。

如果后续做方案 C（LLM 比对 description vs resolution），建议作为**独立功能**，与方案 B 不耦合。
