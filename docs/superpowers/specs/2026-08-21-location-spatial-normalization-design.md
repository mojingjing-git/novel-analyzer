# 地点与空间关系归一化（Phase 0 插入最终总结）

日期：2026-08-21
状态：设计中（已与用户讨论完核心决策，待写完后自审 + 用户审阅）

## 目标

1. 解决 LLM 生成 `locations` 数据的 parent/type 字段严重不一致（实测《烂柯棋缘》889 unique locations 中 130 个有多个 parent、143 个有多个 type）
2. 解决 `spatial_relationships` 字段是自由文本散文而非结构化字段（实测 910 条边有 54 对存在多重不一致描述）
3. 在最终总结阶段插入"Phase 0"自动归一化，复用现有的模型/并发/超时设置
4. 数据归一化结果以独立 normalized 文件 + chapter `_normalized_ref` 标记方式存盘，非破坏性、可回滚
5. 大书（千万字级）通过分批 + consolidation 拆分避免 LLM context 溢出

## 背景与现状

- 当前 `MapPage.vue` 直接从 `chapter_*.json` 聚合 locations，渲染树形布局；数据脏导致节点分散、parent 跳转、type 颜色不一致
- 最终总结已经走 4 阶段流水线（`final_summary.py`），有成熟的 checkpoint / retry / token 统计机制
- 后端已有 `text_utils.is_text_duplicate()` 三层去重工具（normalize + 子串 + SequenceMatcher）
- 配置层 `APIConfig` 已有 `summary_model` / `summary_concurrency` / `summary_timeout` / `summary_thinking_mode` 专用字段，可直接复用

不做本设计的话：可视化无论怎么改都无法解决节点分散、type 颜色乱、spatial relation 无法过滤的根本问题。

---

## 1. 触发与生命周期

**触发**：用户点击"开始总结" → `AnalysisService.start_summary` → `FinalSummaryRunner.run()` → 在 `_init_progress()` 之后、`_run_batch()`（Phase 1）之前调用新方法 `_normalize_phase_0()`。

**生命周期**：
- 若 `output/locations_normalized.json` 已存在且对应章节 mtime 未变 → 跳过整个 Phase 0
- 若 `locations_normalized.json` 不存在或章节有更新 → 走完整流程
- 若 `locations_normalized.json` 存在但 `spatial_relationships_normalized.json` 缺失 → 只跑 0b（用现有 0a 结果）
- Phase 0 失败 → 标记整个总结任务失败，不进入 Phase 1-4（避免后续阶段依赖未归一化数据）

**checkpoint 文件**：
- `final_summary_checkpoint/locations_normalized.json`
- `final_summary_checkpoint/spatial_relationships_normalized.json`
- 与现有 `volume_N.md` / `recon_N.json` / `manifest.json` 同目录

**章节 mtime 检测**：维护 `chapter_mtimes_hash` 字段在 normalized 文件顶部，跑前比较任意章节 mtime 变化 → 触发重跑。

---

## 2. 数据契约

### 2.1 `output/locations_normalized.json`

```json
{
  "schema_version": 1,
  "normalized_at": "2026-08-21T14:00:00Z",
  "model": "MiniMax-M2.7",
  "chapter_mtimes_hash": "abc123...",
  "locations": [
    {
      "canonical_name": "宁安县",
      "aliases": ["宁安县", "宁安县城", "宁安县桐树坊"],
      "parent": "京畿府",
      "type": "县城",
      "description": "大贞王朝下辖县城",
      "chapter_count": 87
    }
  ]
}
```

- `aliases` 必须是字面字符串集合（LLM 输出后做严格校验）
- `parent` /  `type` / `description` 是 LLM 在 group 内投票/合并的结果
- 不存 `chapters` 详细数组（数据可从原 chapter_*.json 聚合恢复，省空间）

### 2.2 `output/spatial_relationships_normalized.json`

```json
{
  "schema_version": 1,
  "normalized_at": "2026-08-21T14:00:00Z",
  "model": "MiniMax-M2.7",
  "relationships": [
    {
      "from": "宁安县",
      "to": "德胜府",
      "direction": "东南",
      "distance_text": "约两三百里",
      "distance_estimate_km": 130,
      "relation_type": "相邻",
      "evidence_chapters": [42, 43]
    }
  ]
}
```

- `from` /  `to` 必须是某个 location 的 `canonical_name`（强制约束，违反则拒收）
- `direction` /  `distance_text` /  `distance_estimate_km` /  `relation_type` 是从原文提取的结构化字段
- `evidence_chapters` 引用原数据出现的章节

### 2.3 `chapter_N_result.json` 顶部新增字段

```json
{
  "_normalized_ref": "locations_normalized.json",
  "_normalized_spatial_ref": "spatial_relationships_normalized.json",
  "core_events": [...],
  ...
}
```

- 仅在归一化完成后追加，不修改其他字段
- 用 `safe_save_json` 原子写（已存在 `json_utils` 工具）

---

## 3. 流水线设计（4 子阶段）

```
Phase 0a-batches:  切片 locations → 并发 LLM 调用
Phase 0a-consol:   跨 batch 同地点合并（1 次 LLM）
Phase 0b-batches:  切片 spatial + canonical 白名单 → 并发 LLM 调用
Phase 0b-dedupe:   同 (from, to) pair 机械去重
```

### 3.1 Phase 0a-batches：locations 规范化（并发）

**Step 1：预聚合（机械，OK）**
```python
def aggregate_locations(raw_locations: list) -> list:
    """
    把所有 chapter_*.json 的 locations 聚合为 group。
    每个 group = 字面同名 + 相似名（SequenceMatcher ≥ 0.85）。
    """
    name_meta = {}  # name → {types, parents, descs, chapters}
    for loc in raw_locations:
        n = loc['name'].strip()
        if not n: continue
        ...

    # Union-Find：相似名聚成 group
    parent_uf = {n: n for n in name_meta}
    names = list(name_meta.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if is_same_location(names[i], names[j]):  # normalize + SequenceMatcher ≥ 0.85
                union(names[i], names[j])
    
    # 按 group 聚合元数据
    groups = []
    for root, aliases in groups_dict.items():
        agg_types, agg_parents, agg_descs, agg_chapters = Counter(), Counter(), set(), set()
        for a in aliases:
            m = name_meta[a]
            agg_types.update(m['types'])
            agg_parents.update(m['parents'])
            agg_descs |= m['descs']
            agg_chapters |= m['chapters']
        groups.append({
            'aliases': sorted(aliases, key=len),
            'types': dict(agg_types),
            'parents': dict(agg_parents),
            'chapter_count': len(agg_chapters),
            'sample_desc': next(iter(agg_descs), ''),
        })
    
    groups.sort(key=lambda g: -g['chapter_count'])  # 高频在前
    return groups
```

`is_same_location(n1, n2)` 规则（极保守，不加 substring 避免短名误伤）：
```python
def is_same_location(n1, n2):
    if n1 == n2: return True
    a = _normalize_for_dedup(n1)
    b = _normalize_for_dedup(n2)
    if a == b: return True
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= 0.85
```

**Step 2：切片**
```python
BATCH_SIZE = 1000
batches = []
current, current_chapters = [], 0
for g in groups:
    current.append(g)
    current_chapters += g['chapter_count']
    if len(current) >= BATCH_SIZE or current_chapters >= 5000:
        batches.append(current)
        current, current_chapters = [], []
if current: batches.append(current)
```

**Step 3：并发 LLM 调用**
```python
async def _run_phase_0a_batches():
    semaphore = asyncio.Semaphore(config.analysis.summary_concurrency)
    
    async def process_one(batch_idx, batch):
        async with semaphore:
            messages = build_location_prompt(batch, batch_idx, len(batches))
            result = await self.llm_client.chat(messages, response_format={'type': 'json_object'})
            return parse_and_validate(result, batch)
    
    tasks = [process_one(i, b) for i, b in enumerate(batches)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_canonicals = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"Phase 0a batch 失败: {r}")
            raise r  # 任一 batch 失败则整 Phase 0 失败
        all_canonicals.extend(r)
    return all_canonicals
```

复用 `summary_concurrency` 作为并发上限（默认 2）。

**LLM Prompt 结构（user message 格式）**：
```
## 待归一化的地点（batch 3/10，共 950 个 group）

[1] aliases=[宁安县, 宁安县城]
    types={城市:2, 城关:1}
    parents={大周王朝:5, 江州:1}
    chapter_count=87
    sample_desc="大贞王朝下辖县城"
...

请按 system prompt 规则输出 JSON {"locations": [...]}
```

### 3.2 Phase 0a-consol：跨 batch 同地点合并（1 次 LLM）

**必要性**：如果"宁安县"在 batch 1、"宁安县城"在 batch 5，0a batches 后会产生两个 canonical（实际同一地点）。**必须用 LLM 跨 batch 识别合并**。

```python
async def _run_phase_0a_consolidate(all_canonicals):
    # 提取 canonical_name + aliases 列表
    canonical_list = [
        {"canonical": c["canonical_name"], "aliases": c["aliases"]}
        for c in all_canonicals
    ]
    
    # 大切片场景（canonical_list > 5000 项）→ 分批 consolidation
    # 每批 ~2000 canonical + 完整 prompt（系统提示固定）
    # 收集所有 batches 的 merge_map 后机械 apply
    CONSOL_BATCH = 2000
    if len(canonical_list) <= CONSOL_BATCH:
        prompt = build_consolidation_prompt(canonical_list)
        result = await self.llm_client.chat([prompt])
        merge_map = parse_merge_map(result)
    else:
        merge_map = {"merges": []}
        for i in range(0, len(canonical_list), CONSOL_BATCH):
            batch_slice = canonical_list[i:i+CONSOL_BATCH]
            prompt = build_consolidation_prompt(batch_slice, i // CONSOL_BATCH + 1)
            result = await self.llm_client.chat([prompt])
            partial = parse_merge_map(result)
            merge_map["merges"].extend(partial["merges"])
    
    # 机械应用 merge_map 到 all_canonicals
    # 语义：对每个 [a, b] 二元组，把 a 的 aliases/types/parents/chapter_count 合并到 b
    #       （或反向，统一选 chapter_count 大的作为保留 canonical_name）
    final_locations = apply_merges(all_canonicals, merge_map)
    return final_locations
```

**LLM Prompt**（system）：
```
你是中文地名归一化审核员。下面是 {N} 个地点的 canonical_name 与 aliases 列表。
请识别哪些组指向**同一个真实地点**（不是同类型，是完全相同的一个地方）。

判断规则：
- 字面别名通常意味着同一地点的不同写法（如 "宁安县" vs "宁安县城"）
- 若 group 内部 aliases 已经包含别名，可能是 pre-aggregation 跨 batch 漏合并
- 不要合并同类型但不同地方的（如 "宁安县城" 与 "宁安县城天牛坊" 不是同一地点）
- 不要合并不同朝代/不同世界的同名地点（如两个故事里的"京城"）

输出 JSON：{"merges": [["宁安县", "宁安县城"], ["大贞", "大贞王朝"], ...]}
merges 是字面字符串二元组列表，每个二元组是同一地点的两个 canonical 写法。
没有需要合并的：{"merges": []}
```

### 3.3 Phase 0b-batches：spatial_relationships 结构化（并发）

**Step 1：预聚合 by (from, to) pair**
```python
def aggregate_spatial_pairs(raw_rels):
    pair_meta = {}  # (from, to) → {relations: set, chapters: set}
    for rel in raw_rels:
        f, t = rel['from'].strip(), rel['to'].strip()
        if not f or not t: continue
        key = (f, t)
        pair_meta.setdefault(key, {'relations': set(), 'chapters': set()})
        pair_meta[key]['relations'].add(rel.get('relation', ''))
        pair_meta[key]['chapters'].add(rel['chapter'])
    
    pairs = [
        {'from': k[0], 'to': k[1], 'relations': list(v['relations']), 'chapters': sorted(v['chapters'])}
        for k, v in pair_meta.items()
    ]
    pairs.sort(key=lambda p: -len(p['chapters']))
    return pairs
```

**Step 2：构造 canonical 白名单**
```python
WHITELIST = [
    {"canonical": c["canonical_name"], "aliases": c["aliases"]}
    for c in final_locations  # 来自 Phase 0a consol
]
# ~5K-10K 项，每项 ~20 chars = ~50K chars = ~15K tokens
```

**Step 3：并发 LLM 调用（白名单共享）**
```python
BATCH_SIZE = 1000
batches = [pairs[i:i+BATCH_SIZE] for i in range(0, len(pairs), BATCH_SIZE)]

async def process_spatial_batch(batch):
    messages = build_spatial_prompt(batch, WHITELIST)
    return parse_and_validate_spatial(await self.llm_client.chat(messages))

tasks = [process_spatial_batch(b) for b in batches]
results = await asyncio.gather(*tasks, return_exceptions=True)
all_spatial = [r for r in results if not isinstance(r, Exception)]
```

**LLM Prompt**（user message 格式）：
```
## 可用地名白名单（{N} 个）
[L1] canonical=宁安县, aliases=[宁安县, 宁安县城, 宁安县桐树坊]
[L2] canonical=居安小阁, aliases=[居安小阁]
...

## 待结构化的空间关系（batch 1/25，共 800 个 pair）

[P1] from=碧岚国, to=齐凉国
    relations=["两国接壤...", "水师借道..."]
    chapters=[102, 156, 230]

[P2] from=齐凉国, to=大贞
    relations=["位于大贞西北方向...", "约十天航程"]
    chapters=[42, 103, 156]
...
```

**from / to 强制约束**：输出必须字面引用白名单中的 canonical_name，不许创造新地名。

### 3.4 Phase 0b-dedupe：机械去重

```python
def dedupe_spatial_pairs(all_spatial):
    """同 (from, to) 对的多 batch 结果合并"""
    pair_meta = {}
    for rel in all_spatial:
        key = (rel['from'], rel['to'])
        if key not in pair_meta:
            pair_meta[key] = rel
        else:
            # 保留 evidence_chapters 最全的那条
            existing = pair_meta[key]
            if len(rel['evidence_chapters']) > len(existing['evidence_chapters']):
                pair_meta[key] = rel
            else:
                existing['evidence_chapters'] = sorted(set(
                    existing['evidence_chapters'] + rel['evidence_chapters']
                ))
    return list(pair_meta.values())
```

### 3.5 校验链

**Phase 0a 校验**：
1. JSON 合法（`safe_parse_json` 8 级容错链）
2. Schema 合规（必填字段齐全）
3. **canonical_name 必须字面等于 aliases 中的某一个**
4. 任一校验失败 → 该条拒收，记录 warn log；如果某 batch 整体失败率 > 20% → 重试该 batch（温度 -0.1，最多 2 次）

**Phase 0a-consol 校验**：
- merges 中的每个字符串必须出现在 canonical_list 中（防幻觉）
- 任一校验失败 → 重试（温度 -0.1，最多 2 次）

**Phase 0b 校验**：
1. JSON 合法
2. Schema 合规
3. **from / to 必须字面等于白名单中的某个 canonical**
4. evidence_chapters 非空且全部是合法整数

---

## 4. 配置复用

直接复用 `APIConfig` 已有字段，**不新增任何配置项**：

| 用途 | 复用字段 | 默认值 |
|---|---|---|
| 模型 | `summary_model`（空则用 `model`） | "MiniMax-M2.7" |
| 思考模式 | `summary_thinking_mode`（空则用 `thinking_mode`） | {} |
| 并发数 | `summary_concurrency` | 2 |
| 单次超时 | `summary_timeout` | 较长（30 分钟级） |

**理由**：归一化是总结阶段任务，用户期望与总结共享重型模型 / 高并发 / 长超时配置。设置页的"总结"区块已经暴露这些字段，无需 UI 改动。

---

## 5. 文件改动清单

| 文件 | 类型 | 改动 |
|---|---|---|
| `backend/services/location_normalizer.py` | **新建** | `LocationNormalizer` 类：`aggregate_locations`、`aggregate_spatial_pairs`、`is_same_location`、`build_location_prompt`、`build_spatial_prompt`、`build_consolidation_prompt`、`parse_and_validate`、`_run_phase_0a_batches`、`_run_phase_0a_consolidate`、`_run_phase_0b_batches`、`_run_phase_0b_dedupe` |
| `backend/services/final_summary.py` | 改 | `FinalSummaryRunner.__init__` 接收 `LocationNormalizer`；`run()` 在 `_init_progress` 后、`_run_batch` 前插入 `_normalize_phase_0()` |
| `backend/services/queue_service.py` | 改 | `_run_final_summary_for_book` 创建 `FinalSummaryRunner` 时同时创建 `LocationNormalizer` |
| `backend/services/viz_service.py` | 改 | `map_data()` 加 `_normalized_ref` 检测：有则读 `locations_normalized.json`；无则走老逻辑 |
| `backend/utils/text_utils.py` | **新建函数** | `is_same_location`（保守规则：normalize + SequenceMatcher ≥ 0.85） |
| `backend/tests/test_location_normalizer.py` | **新建** | mock LLM 跑校验链；测试 group 聚合 / consolidation / dedupe |
| `agent.md` | 改 | §5 加新章节描述 `LocationNormalizer`；§10 加新变更记录 |

**UI 改动**：无（透明集成在总结阶段）。

---

## 6. 验证清单

### 6.1 单元测试
- `test_aggregate_locations`：输入 100 个原始 location（含别名），输出 N 个 group，group 数 < unique 名数
- `test_is_same_location`：边界用例（"宁安县" vs "宁安县城" → False；"居安小阁" vs "居安小阁（主角）" → True）
- `test_parse_and_validate`：故意构造 LLM 幻觉输出（canonical 不在 aliases 中） → 该条拒收
- `test_consolidate`：输入 800 个个 canonical，输出 merge_map 无幻觉
- `test_dedupe_spatial_pairs`：同 (from, to) 多 batch → 合并 evidence_chapters

### 6.2 集成测试
- 拿《烂柯棋缘》output 跑一次 Phase 0 → 验证：
  - 输出文件落盘
  - 每个 chapter_*.json 加了 `_normalized_ref`
  - viz_service.map_data() 走新分支，数据可视化改善
- 对比 normalized vs raw 的 location 数、parent 多样性

### 6.3 全量验证
- py_compile 后端
- pytest 全部通过
- vue-tsc（无 UI 改动，预期通过）
- npm run build（同上）

### 6.4 用户验收（沙箱外）
- 选 200 万字级别书跑一次总结 → 打开 MapPage → 节点数应明显减少、type 颜色更统一
- 选千万字级别书（如有）跑 → 不溢出、consolidate 正确合并跨批同地点
- 章节 mtime 变化 → 第二次跑总结自动重做归一化
- 删除 `_normalized_ref` 字段 → 立即回滚到老可视化

---

## 7. 注意事项

1. **不可修改 raw chapter_*.json 的原有字段**（core_events / character_arcs / 等），仅追加 `_normalized_ref`
2. **CRLF/LF 历史差异保留**，不批量格式化（与 §9.5 硬性约束一致）
3. **不引入新第三方库**（直接用 `SequenceMatcher` 已在 `text_utils.py`）
4. **不修改 `MapPage.vue`**（数据干净了，原有 SVG 树形布局也能用；如要升级到 ECharts 后续另开 spec）
5. **本设计不动 `aggregate_utils.JSONAggregator`**，仅在 `viz_service.map_data()` 检测 `_normalized_ref` 后改读 normalized 文件
6. **容灾**：Phase 0 任一阶段失败 → checkpoint 保留已有部分（partial 状态）→ 用户可手动决定重跑或跳过
7. **backward compat**：老数据（没有 `_normalized_ref`）继续可用，老 MapPage 行为不变

---

## 8. 数据流总图

```
分析完成 → chapter_*.json 落盘 ↓
用户点"开始总结"
    ↓
FinalSummaryRunner.run()
    ↓
_init_progress()
    ↓ Phase 0 ← 新增
[0a-batches] 预聚合 + 切片 + 并发 LLM 调 locations
    ↓
[0a-consol] 1 次 LLM 合并跨 batch 同地点
    ↓
[0b-batches] 并发 LLM 调 spatial + canonical 白名单
    ↓
[0b-dedupe] 机械去重同 (from,to) 对
    ↓
落盘：
    - output/locations_normalized.json
    - output/spatial_relationships_normalized.json
    - 改写每章 chapter_*.json，加 _normalized_ref 字段
    - checkpoint 落盘 final_summary_checkpoint/
    ↓ Phase 1-4 不动
[1] 分卷摘要 + 伏笔调和
[2] 全书伏笔复检
[3] 风格提取
[4] 最终报告
    ↓
总结完成 ↓
MapPage.vue / viz_service.map_data() 读时：
    ├─ 见 _normalized_ref → 读 normalized 文件
    └─ 无 → 走老聚合逻辑
```

---

## 9. 决策记录

| 决策点 | 选择 | 理由 |
|---|---|---|
| 触发时机 | 总结时自动跑 | 用户最简；token 成本可控 |
| 归一化范围 | location + spatial 都处理 | 用户决策 |
| 输出位置 | 独立 normalized.json + _normalized_ref | 非破坏、可回滚 |
| Type 词汇表 | LLM 动态生成 | 用户决策；不需人工设计 |
| 批次大小 | 1000 group / batch | 经验值，单 batch ~30K token |
| 0a/0b 是否分开 | 是 | 注意力 + 跨引用准确率 |
| 千万字是否再拆 | 是（4 子阶段） | 避免 context 溢出 |
| 0a batches 串/并 | 并发（asyncio.gather + 信号量） | 用户决策；不显著拖慢 |
| 模型/超时/并发设置 | 复用 summary_* 字段 | 用户决策；无 UI 改动 |
| 是否改 MapPage.vue | 不改 | 数据修复后原布局可用 |
| 是否动 aggregate_utils | 不动 | 只改 viz_service 读路径 |

---

## 10. 未来工作（非本 spec）

1. 归一化数据上线后用户再决定是否升级 MapPage.vue 到 ECharts 树形 + 章节切片
2. 归一化质量监控：在 SummaryPage 加 Phase 0 token 用量展示
3. 增量归一化：新章节分析完成后增量跑归一化（避免每次重跑全量）