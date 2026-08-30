# 审核报告 #8：伏笔去重升级（字符级 → 语义级）

> 审核日期：2026-08-27
> 审核范围：`backend/utils/text_utils.py` `deduplicate_foreshadows` / `_extract_keywords` / `_normalize_for_dedup`、`backend/services/final_summary.py` `_build_foreshadow_catalog`、`backend/utils/foreshadow_ledger.py`、`backend/config/constants.py` `FORESHADOW_CATEGORY_DEFS`
> 改进方向：用"关键词倒排索引 + Jaccard ≥0.5"作为主去重策略，保留现有 SequenceMatcher ≥0.6 作兜底

---

## 1. 源码调研摘要

### 1.1 现状：PERF-2 已实现倒排索引 + 关键词交集 + SequenceMatcher

`backend/utils/text_utils.py:228-371` `deduplicate_foreshadows`

**两阶段去重流程**：

```python
# 阶段 1：预处理（每条 entry 提取关键词 + 归一化文本）
for item in clues:
    entries.append({
        'chapter': chapter,
        'clue': str(clue_text).strip(),
        'keywords': _extract_keywords(str(clue_text)),  # 2-4 字关键词集合
        'normalized': _normalize_for_dedup(str(clue_text)),  # 去除虚词标点
        'type': str(ftype) if ftype else "",
        'confidence': str(conf) if conf else "",
        'importance': str(imp) if imp else "",
    })

# 阶段 2：分组（PERF-2 倒排索引 + 关键词交集 + SequenceMatcher 精筛）
groups = []
keyword_index: Dict[str, set] = {}  # 关键词 -> 含该关键词的组号集合

for i, entry in enumerate(entries):
    candidates = set()
    for kw in entry['keywords']:
        candidates |= keyword_index.get(kw, set())  # 只与共享关键词的组比较

    for g_idx in sorted(candidates):
        rep = entries[groups[g_idx][0]]  # 取组内首条作代表
        overlap = entry['keywords'] & rep['keywords']
        if len(overlap) < keyword_overlap_min:  # 默认 2（粗筛）
            continue
        ratio = _difflib.SequenceMatcher(
            None, entry['normalized'], rep['normalized']
        ).ratio()
        if ratio >= similarity_threshold:  # 默认 0.6（精筛）
            groups[matched_group].append(i)
            break
    # 否则新建组
    for kw in entry['keywords']:
        keyword_index.setdefault(kw, set()).add(g)
```

**关键观察**：
1. **PERF-2 倒排索引已实现**（`text_utils.py:295-308`）：长书数千条线索时不再是 O(N²)，只与共享关键词的组比较
2. **粗筛 `keyword_overlap_min=2`**：两条线索共享 2+ 个 2-4 字关键词即视为候选重复
3. **精筛 SequenceMatcher ≥0.6**：候选对在归一化文本上的字符级相似度
4. 已有完整单测：`test_optimizations.py:82-95` `test_deduplicate_foreshadows_index_semantics`

### 1.2 改进方向的具体技术解读

**新主策略**："关键词倒排索引 + Jaccard ≥0.5" 替代 SequenceMatcher 精筛

| 项目 | 现状 | 改进 |
|------|------|------|
| 粗筛 | 关键词交集 ≥2 | Jaccard = \|A∩B\| / \|A∪B\| ≥ 0.5 |
| 精筛 | SequenceMatcher ≥0.6 | **保留作兜底**（双保险） |
| 行为差异 | 共享 2 词即候选，ratio 0.6 即合并 | 共享比例 ≥50% 才候选（更严格） |

**潜在差异案例**：
- 短线索 A：`keywords = {噬血珠, 佛印}`（2 个词）
- 长线索 B：`keywords = {噬血珠, 佛印, 减弱, 禁制, 松动}`（5 个词）
- 现状：A∩B=2，A∪B=5，overlap=2 通过粗筛；SequenceMatcher 算字符级相似
- 新版：Jaccard = 2/5 = 0.4 < 0.5，不通过粗筛 → 两条线索**不合并**
- **新策略可能"漏合并"短-长配对**

### 1.3 与其它模块的耦合点

1. **唯一调用方**：`backend/services/final_summary.py:862` `_build_foreshadow_catalog` 中：
   ```python
   catalog = deduplicate_foreshadows(categorized)  # 6 元组：ch, clue, ftype, conf, imp, category
   ```
2. **结果下游**：
   - 排序：`catalog.sort(key=lambda c: c["_sort_key"], reverse=True)` (final_summary.py:873)
   - 分层截断：按 importance 分组（高/中/其他），分别取前 N
   - 注入 prompt：BATCH_USER_TEMPLATE 的 `{foreshadow_catalog}` 占位符
   - 伏笔账本（`foreshadow_ledger.py`）只接收 `id` 字段，**不关心去重算法**
3. **50 类分类体系**：去重发生在 `category` 解析**之后**——只对 clue 文本做去重，与 type/category 字段正交
4. **DORMANT 状态机**（`foreshadow_ledger.py:103-131`）：仅依赖 `last_seen_chapter`，与去重算法无关

### 1.4 已有测试覆盖

`test_optimizations.py:82-95` `test_deduplicate_foreshadows_index_semantics`：

```python
clues = [
    (1, "噬血珠佛印减弱，禁制松动", "道具伏笔", "高"),
    (5, "噬血珠佛印进一步减弱", "道具伏笔", "高"),
    (2, "田灵儿御剑飞过青云山", "情节伏笔", "中"),
]
catalog = deduplicate_foreshadows(clues)
# 验证：前两条应合并（merged_count=2），第三条独立（merged_count=1）
```

**覆盖强度**：3 条线索的极简用例，足以验证"合并/独立"二元语义，但**不足以覆盖**长书真实分布（数百条线索中各种重叠模式）。

---

## 2. 多角度评分

| 维度 | 评分（1=最低，5=最高） | 说明 |
|------|----------------------|------|
| 改进难度 | 1 | 改动集中在一处（`deduplicate_foreshadows` 内部），主逻辑 ~10 行；PERF-2 倒排索引已实现 |
| 改进收益 | 2 | 实际"问题"是否存在存疑：现状 SequenceMatcher 0.6 在长书已工作良好，PERF-2 优化后性能不是瓶颈；Jaccard 替代后"语义级"提升无明显证据 |
| 代码复杂度提升 | 1 | 加 Jaccard 计算（\|A∩B\| / \|A∪B\|），保留 SequenceMatcher 兜底，净增 ~5 行 |
| 维护难度 | 2 | 双阈值参数化（粗筛 Jaccard + 兜底 SequenceMatcher），需注释说明触发顺序 |
| 兼容性风险 | 1 | 不修改任何下游（catalog 结构、账本、prompt 注入都正交） |
| 测试覆盖成本 | 2 | 扩 `test_optimizations.py` 即可：增加短-长配对、跨章节合并、边界阈值用例 |
| 实施风险 | 2 | Jaccard 阈值（0.5）可能误合并/漏合并，但因 SequenceMatcher 兜底存在，最坏情况是"不优于现状" |

**综合优先级**：**低**。投入产出比偏低——1-2 分的改进收益对应 2 分的测试覆盖成本和 2 分的阈值调参风险。

---

## 3. 关键发现

### 3.1 现状已经"足够好"

`text_utils.py:295-296` 的注释明确：

> PERF-2：关键词倒排索引替代"与全部已有组逐一比较"（原实现 O(N²)，长书数千条线索时需做百万级关键词交集 + SequenceMatcher 精筛）；现在只与共享 ≥1 个关键词的组比较，语义与旧逻辑等价（粗筛阈值不变）。

也就是说：
- **性能问题已解决**（O(N²) → O(N + 候选)）
- **语义等价**（粗筛阈值仍为 2）
- **精筛是 SequenceMatcher 字符级相似**（不是真正的"语义"）

"语义级去重"在中文短文本（20-50 字的伏笔 clue）上的效果**存疑**。字符级 SequenceMatcher 已经能 catch 大多数"同义改写"——例如"噬血珠佛印减弱"和"噬血珠佛印进一步减弱"通过"佛印"+"噬血珠"+"减弱"三个共享关键词触发粗筛，再通过归一化文本的字符级匹配。

### 3.2 Jaccard 替代的潜在问题

Jaccard = |A∩B| / |A∪B| 反映"集合相似度"，对集合大小差异敏感：

| A 关键词数 | B 关键词数 | 交集 | Jaccard | 是否过 0.5 |
|------------|------------|------|---------|------------|
| 2 | 2 | 2 | 1.0 | ✅ |
| 2 | 4 | 2 | 0.5 | ✅（临界） |
| 2 | 5 | 2 | 0.4 | ❌ 漏合并 |
| 3 | 6 | 3 | 0.5 | ✅（临界） |
| 3 | 8 | 3 | 0.375 | ❌ 漏合并 |

**问题**：
- LLM 输出的伏笔 clue 长度差异很大（最短可能 5 字、最长 50+ 字），对应关键词数差异大
- Jaccard 0.5 阈值对"短-长配对"过于严格
- 现状粗筛"交集≥2"反而对短-长配对更友好

### 3.3 "保留 SequenceMatcher 兜底"的实际作用

如果主策略是 Jaccard ≥0.5 粗筛 + SequenceMatcher ≥0.6 兜底：
- Jaccard 通过 → 直接合并
- Jaccard 不通过但 SequenceMatcher 通过 → 合并
- 两者都不通过 → 不合并

这等价于"Jaccard 通过 OR SequenceMatcher 通过"——比单用 SequenceMatcher 更激进（更容易合并）。**实际效果是"去重更激进"**，与改进方向描述的"语义级更准确"目标**不一致**。

如果用户期望的是"减少误合并"（避免把不相关的伏笔合并），单纯加 Jaccard 反而**反方向**。

### 3.4 真正需要的是"同义改写"检测

LLM 在不同章节对同一伏笔的描述可能：
- 章节 5：`"林凡在青云山发现噬血珠"`
- 章节 18：`"林凡从藏经阁取出古剑（疑似噬血珠）"`
- 章节 30：`"噬血珠认主"`

这些**字符级相似度都很低**（共享关键词只有"噬血珠"），但**是同一伏笔**。这种 case 现状和 Jaccard 都无法合并。

**真正的"语义级"需要**：LLM embedding / 余弦相似 / 预训练模型。这超出"不加新依赖"的约束。

### 3.5 实际可观察的痛点

`grep "foreshadow"` 全 backend 目录，没有发现"用户反馈去重质量差"的 issue 描述。CHANGELOG 中只有 PERF-2 性能修复，**没有"去重质量"修复**。

也就是说：
- 性能问题：已修（PERF-2 倒排索引）
- 质量问题：未报告
- **本改进可能是"未发现问题前的预防"**

---

## 4. 实施风险

### 4.1 失败模式

1. **Jaccard 阈值 0.5 偏严**：漏合并"短-长配对"伏笔 → catalog 中出现重复伏笔 → 用户报告"伏笔没合并"
2. **Jaccard 阈值 0.5 偏松**：合并本应独立的伏笔 → catalog 中一条伏笔包含多个不同线索 → 后续 reconciliation 误判
3. **保留 SequenceMatcher 兜底等于更激进合并**：与"减少误合并"目标相反
4. **新参数破坏现有测试**：`test_optimizations.py:82-95` 现有用例必须仍然通过（合并/独立二元语义不变）——如果 Jaccard 主策略改变了行为，需更新测试

### 4.2 边界条件

- 关键词集合为空（_extract_keywords 返回空集）：Jaccard = 0/0 = 报错；需先判空
- 两条线索完全相同：交集=并集，Jaccard=1.0
- 两条线索完全无共享：交集=0，Jaccard=0
- clue 极短（< 4 字）：_extract_keywords 可能不返回任何词（中文连续片段长度 < min_len=2）→ 关键词集合空
- clue 极长（> 100 字）：关键词集合巨大（每个 2-4 字片段都是关键词），倒排索引仍高效但 Jaccard 分母变大

### 4.3 与其它模块的交互

- **PERF-2 倒排索引**：仍保留，不变。Jaccard 计算是"组代表与新条目之间的组级计算"，不破坏倒排索引结构
- **`_normalize_for_dedup`**（L188-194）：归一化逻辑（去停用词、标点）保留不变
- **`_extract_keywords`**（L197-225）：提取 2-4 字中文片段逻辑保留不变
- **伏笔账本**：接收 catalog 后的 `id` 字段是 `f"fs_{g_idx + 1:03d}"`（L360），去重算法变化只影响 g_idx 编号顺序，账本不感知
- **prompt 注入**：`{foreshadow_catalog}` 占位符是 `BATCH_USER_TEMPLATE` (final_summary.py:98)，去重算法变化只影响 catalog 内容质量

---

## 5. 兼容性影响

### 5.1 对断点续跑的影响

无影响。`deduplicate_foreshadows` 仅在 `_build_foreshadow_catalog`（final_summary 阶段）调用，不在 analyze 阶段。断点续跑不涉及。

### 5.2 对旧书分析结果的影响

- **catalog 内容会变化**：Jaccard 主策略下合并/独立结果可能与现状不同
- **账本影响**：账本 ID 编号顺序变化（g_idx 改变）→ 账本中伏笔 ID 可能重新映射
- **伏笔总表持久化**：`_build_foreshadow_catalog` 不直接落盘，账本状态机持久化在 `foreshadow_ledger.json`
- 实际影响程度：低到中。多数伏笔会保持原合并/独立状态，但**少量边界 case 会变化**。

### 5.3 对 KB 数据结构的影响

无影响。`kb.foreshadowing_network` 字段（`models/knowledge.py:26`）是"第N章: 文本"字符串拼接，不含去重后 catalog。

### 5.4 对伏笔账本状态机的影响

- 账本 ID 编号（`fs_001`, `fs_002`, ...）依赖 catalog 中 g_idx，去重算法变化可能让 ID 重新映射
- 状态机（active/resolved/dormant）依赖 `last_seen_chapter` 和 `current_chapter`（`foreshadow_ledger.py:103-131`），与去重算法正交
- **重启后账本加载**：账本从 `foreshadow_ledger.json` 加载，已持久化的 ID 不会因为去重算法变化而变化；**但**重建账本（重新跑 final_summary）会得到新 ID
- 影响程度：低。建议在 CHANGELOG 中提示"重建账本后伏笔 ID 可能变化"。

### 5.5 对 50 类分类体系的影响

无影响。去重只比对 clue 文本，不涉及 type/category 字段。

---

## 6. 建议优先级

**优先级：低**。改进收益（2 分）低于实施成本（测试 2 分 + 阈值调参 2 分），且现状已"足够好"。

理由：
- 性能问题已修（PERF-2 倒排索引）
- 质量问题未报告（无用户痛点）
- "语义级去重"超出 SequenceMatcher 字符级范围，需要 embedding/模型，超出本改进方向约束
- Jaccard 替代可能反向（更激进合并）—— 与改进意图不一致

**实际建议**：推迟本改进。如果未来有用户明确反馈"伏笔去重质量差"（如"同义改写未合并"或"不同伏笔被误合并"），再针对具体 case 调参。

---

## 7. 替代方案或简化版

### 7.1 零变更版（推荐）

**不改 deduplicate_foreshadows**。现状（关键词倒排 + 交集 ≥2 + SequenceMatcher ≥0.6）已能 catch 90% 的伏笔重复。

**评估**：收益 0 分，难度 0 分，风险 0 分。**最务实**。

### 7.2 纯参数调优版

不改算法逻辑，只调整现有阈值：
- `keyword_overlap_min` 从 2 降到 1（更激进合并）—— 风险：误合并
- `similarity_threshold` 从 0.6 降到 0.5（更激进合并）—— 风险：误合并

**评估**：收益 1 分（仅调参），难度 1 分，风险 3 分（误合并）。**比零变更版差**。

### 7.3 Jaccard 单独版（如果坚持改进）

只用 Jaccard 替代 SequenceMatcher，去掉兜底：
```python
# 替换 L318-324
rep_keywords = rep['keywords']
union = entry['keywords'] | rep_keywords
if not union:
    continue  # 双方都无关键词，跳过
jaccard = len(overlap) / len(union)
if jaccard >= 0.5:
    matched_group = g_idx
    break
```

**评估**：收益 1 分，难度 1 分，风险 2 分。比零变更版略差。

### 7.4 真正的语义级版（如果未来有需求）

引入轻量 embedding：
- 中文用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`（~100MB）
- 对每条 clue 计算 embedding
- 余弦相似度 ≥ 0.85 视为重复
- 缓存 embedding 到 chapter_N_result.json 旁的小文件，避免重算

**评估**：收益 5 分（真正语义级），难度 4 分（新依赖 + embedding 索引），风险 3 分。**超出本改进方向**。

---

## 8. 实施路径

### 方案 A：零变更（推荐）

不做任何代码改动。理由：
- 现状已足够好
- 改进方向未发现真实痛点
- Jaccard 替代有反向风险（更激进合并）

如有时间，可补充：
- 在 `test_optimizations.py` 中增加更多 fixture（用真实已分析书的数据采样）增强测试覆盖
- 在 AGENTS.md 记录"伏笔去重算法演进史"和参数调优经验

### 方案 B：Jaccard 渐进版（如果用户坚持改进）

1. 在 `text_utils.py` 新增：
   ```python
   def _jaccard_keywords(a: set, b: set) -> float:
       """关键词集合的 Jaccard 相似度。空集返回 0.0。"""
       if not a or not b:
           return 0.0
       union = a | b
       return len(a & b) / len(union)
   ```
2. 在 `deduplicate_foreshadows` 中：
   - 主策略改为 Jaccard ≥0.5
   - **保留** SequenceMatcher ≥0.6 作兜底（注释说明"防御短-长配对漏合并"）
   - 新增参数 `jaccard_threshold: float = 0.5`
3. 扩 `test_optimizations.py`：
   - 加 Jaccard 边界用例（0.5 临界）
   - 加短-长配对（确保 SequenceMatcher 兜底生效）
   - 加完全相同/完全不同文本
4. 跑 13 本真实书的 final_summary，对比合并前后 catalog 差异
5. CHANGELOG 增加"伏笔去重 Jaccard 升级"条目

### 方案 C：参数化 + 配置化版

把 Jaccard threshold / SequenceMatcher threshold 暴露到 `settings.py`，让用户可调：
- 默认 Jaccard 0.5、SequenceMatcher 0.6
- 用户可改为 Jaccard 0.6 + SequenceMatcher 0.5（更严格）

**评估**：收益 1 分，难度 2 分，风险 2 分。比方案 B 多配置灵活性，但实际价值低（用户不会主动调参）。

### 方案 D：真实语义级版（未来需求）

如果未来有明确"同义改写"合并需求：
- 引入 `sentence-transformers`（或 `modelscope` 中文模型）
- 缓存 embedding 到 `foreshadow_embeddings.json`（per-book）
- 用 FAISS / Annoy 做近似最近邻
- 余弦相似度 ≥ 0.85 视为重复

**预计工时**：
- 方案 A：0 小时
- 方案 B：2-3 小时（含测试 + 真实数据回测）
- 方案 C：3-4 小时
- 方案 D：1-2 天（首次集成 embedding 框架）

**综合推荐**：方案 A（零变更）。如果团队有共识要改进，方案 B 是可接受的折中。
