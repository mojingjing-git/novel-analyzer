# 审核报告 #5：角色名归一化前移到分析阶段

> 审核日期：2026-08-27
> 审核范围：`backend/core/memory_state.py` `_merge_result_into`、`backend/core/knowledge_base.py` `_merge_analysis_results` / `_merge_incremental`、`backend/utils/aggregate_utils.py` `_normalize_char_name`、`backend/models/analysis_result.py` `from_dict`、相关测试
> 改进方向：把"剥离角色后缀（林凡道→林凡、（主角）→裸名）"从聚合阶段（`aggregate_utils`）前移到合并阶段（`memory_state._merge_result_into`），让 KB、prompt、聚合三层共享同一份归一化结果

---

## 1. 源码调研摘要

### 1.1 现状：归一化只在聚合阶段执行

**归一化函数位置**：`backend/utils/aggregate_utils.py:20-35`

```python
_CHAR_NAME_SUFFIXES = (
    '（主角）', '（配角）', '（已故）', '（重要）', '（次要）',
    '（女主）', '（男主）', '（反派）', '（龙套）',
    '(主角)', '(配角)', '(已故)', '(重要)', '(次要)',
    '(女主)', '(男主)', '(反派)', '(龙套）'
)

def _normalize_char_name(name: str) -> str:
    """归一化角色名：去除常见括号标注后缀，用于dict key去重"""
    name = name.strip()
    for suffix in _CHAR_NAME_SUFFIXES:
        if name.endswith(suffix):
            base = name[:-len(suffix)].strip()
            if base:
                return base
    return name
```

**唯一调用点**：`backend/utils/aggregate_utils.py:308, 339, 350`（三处均位于 `JSONAggregator.aggregate_character_tracking` 内部）

```python
# L308 — core_events.characters 拆分后归一化
for char in chars:
    key = _normalize_char_name(char)
    ...
# L339 — character_arcs.name 归一化
arc_key = _normalize_char_name(arc.name)
# L350 — character_arcs.name 再次归一化（state_history）
arc_key = _normalize_char_name(arc.name)
```

### 1.2 关键发现：4 处合并逻辑都没归一化

| # | 文件 | 行号 | 关键代码 | 后果 |
|---|------|------|----------|------|
| 1 | `memory_state.py` | 386 | `kb.character_states[arc.name] = arc.surface_action` | 同角色"林凡"和"林凡道"产生两条 state 记录 |
| 2 | `memory_state.py` | 394 | `pair_key = f"{first.name}-{second.name}"` | 配对 key 不归一化，A-B 与 A-林凡道 两条记录并存 |
| 3 | `knowledge_base.py` | 368 | `kb.character_states[arc.name] = arc.surface_action` | 同样问题（脚本合并路径） |
| 4 | `knowledge_base.py` | 378 | `pair_key = f"{first.name}-{second.name}"` | 同样问题 |
| 5 | `knowledge_base.py` | 450 | `_merge_incremental` 增量路径 | 同样问题 |
| 6 | `knowledge_base.py` | 458 | `_merge_incremental` 配对 | 同样问题 |

**典型污染案例**：
- LLM 第 1 章输出 `character_arcs: [{name: "林凡（主角）", ...}]` → 写 `character_states["林凡（主角）"] = "..."`
- LLM 第 5 章输出 `character_arcs: [{name: "林凡", ...}]` → 写 `character_states["林凡"] = "..."`（**不覆盖**前者！）
- 最终 `character_states` 出现两条记录，且 `_merge_result_into` 中"最后一次 surface_action 覆盖"语义被破坏

### 1.3 与其它模块的耦合点

1. **聚合层 `aggregate_utils.py`**：是当前**唯一**归一化执行方，输出 `character_tracking_aggregated.json` 时才归一化
2. **持久化层 `chapter_N_result.json`**：落盘的是**原始未归一化**的 `name` 字段；重启后从 JSON 读回依然带括号后缀
3. **prompt 构建层 `prompt_builder.py`**：把 `kb.character_states` 注入下一章 prompt 时，dict key 未归一化 → prompt 中出现"林凡"和"林凡道"两版（已知 prompt token 浪费问题）
4. **数据可视化层**：`viz_service.py`、`character_graph.py`、`character_card_generator.py` 三个下游消费者从 `character_tracking_aggregated.json` 读取归一化后的 key，但若直接读 `kb.character_states`（未归一化）则污染
5. **API 层 `routes_foreshadow.py`**：不涉及 character 字段，但 KB 持久化结构若有变化需兼容 schema

### 1.4 已有测试覆盖

**几乎没有**。`grep _normalize_char_name` 命中 4 处全部位于 `aggregate_utils.py` 内部（定义 + 3 处调用），无任何单测文件引用此函数。`test_aggregate_atomic.py` 用的最小数据 `{"core_events": [{"characters": "..."}]}` 也未覆盖 character_arcs.name 的归一化路径。

可对比参照的覆盖：
- `test_optimizations.py:82-95` 覆盖 `deduplicate_foreshadows` 倒排索引（PERF-2 回归）
- `test_location_normalizer.py` 覆盖 `is_same_location` 地名归一化
- `test_models.py:184-201` 覆盖 `validate_parsed_analysis` 4 字段校验

---

## 2. 多角度评分

| 维度 | 评分（1=最低，5=最高） | 说明 |
|------|----------------------|------|
| 改进难度 | 3 | 函数本身简单（10 行），但需在 6 处合并点统一接入，且要处理 "归一化空结果兜底" 边界 |
| 改进收益 | 4 | 一次性修复 KB 准确性、prompt 注入清洁度、聚合输出准确性三个下游；并且 chapter_N_result.json 落盘前已正确 |
| 代码复杂度提升 | 2 | 入口点 + 1，6 处合并点同步改；可选拆出 `_normalize_or_self(name) -> str` 工具函数 |
| 维护难度 | 2 | 后缀表挪到 `text_utils.py`（已有 `_PREFIX_MODS`/`_SUFFIX_MODS` 模式），单点维护 |
| 兼容性风险 | 4 | 旧 `chapter_N_result.json` 数据已固化未归一化名字；KB 重建会让 `character_states` / `character_relationships` 内容漂移（旧书的"林凡"和"林凡道"两条记录会合并成一条） |
| 测试覆盖成本 | 3 | 需覆盖 6 处合并路径 + 旧数据兼容（fixture 对比） + `merge_one` 增量合并时归一化稳定性 |
| 实施风险 | 3 | 并发补跑场景：低章号块迟到时不归一化→归一化，可能与已归一化的 KB 状态产生不对称 |

**综合优先级**：**中高**。收益明显（4 分）但兼容性风险（4 分）需要明确的迁移方案。

---

## 3. 关键发现

### 3.1 KB 污染最严重的不是聚合层

很多人以为"角色名去重"是聚合层的问题，实际上 **KB 层污染**才是最严重的：

- `kb.character_states` 的覆盖语义（"最后一次 surface_action 覆盖"）依赖"同一角色用同一 key"假设。当 key 出现"林凡"和"林凡（主角）"两版时，覆盖语义被破坏，最终可能存留"林凡（主角）"版（首次出现最迟但被覆盖）。
- `kb.character_relationships` 的配对 key `"A-B"` 也会出现"A-林凡（主角）"和"林凡-A"两版。

### 3.2 聚合层归一化是"事后补救"

`aggregate_character_tracking` 的归一化只能让 `character_tracking_aggregated.json` 输出干净，**不能修复 KB 内部污染**。也就是：
- 用户从 KB 导出 / 看 prompt 时：污染版本
- 用户从聚合 JSON 导出 / 看可视化时：归一化版本

两边数据不一致是当前**已知设计缺陷**。

### 3.3 prompt 注入层"自我循环污染"

`prompt_builder.py` 把 `kb.character_states` 注入下一章 prompt。如果 KB 中"林凡"和"林凡（主角）"两版都有，LLM 在下一章会看到两个名字 → 倾向于分别输出两版 character_arc → 进一步污染下一章 KB。**这是一个正反馈循环**。

### 3.4 后缀白名单本身已经相对保守

`_CHAR_NAME_SUFFIXES` 只覆盖"括号修饰符"（主角/配角/已故/重要/次要/女主/男主/反派/龙套），**未覆盖**：
- 文中"林凡道""林凡曰"等叙述后缀（来自古文/网文叙述体"XX道"）
- "林凡（20岁）"年龄后缀
- "林凡（化神期）"境界后缀

如果用户期望是"全场景归一化"，需要扩白名单。如果只期望"括号后缀归一化"，现状功能足够——但**前移仍有价值**（让 KB 干净）。

---

## 4. 实施风险

### 4.1 失败模式

1. **旧书 KB 不自愈**：旧书的 `chapter_N_result.json` 落盘时未归一化，重新加载 + `restore_from_disk` 走 `_merge_result_into` 时**会在内存 KB 中归一化**，但**磁盘 JSON 不会自动重写**。重启后又从原始 JSON 加载 → 内存归一化 → 落盘时（如果 flush）覆盖原始 JSON 为归一化版 → 旧数据被静默改写。
2. **增量合并不对齐**：并发补跑时低章号块迟到（`_merge_one` 中 `in_order=False`），归一化可能在已归一化 KB 和未归一化低章号块之间产生不一致（虽然结果应该一致，但 seen_arcs 集合的"已见性"判定会被影响）。
3. **空名兜底**：当前 `_normalize_char_name` 在 base 为空时返回原 name（`if base: return base`），即 `"（主角）"` 会被原样保留为 key。KB 层归一化需要一致的空名策略——是丢弃？是保留原 name？这需要在改进时显式决策。
4. **character_arcs 与 core_events.characters 拆分不一致**：`_normalize_char_name` 在两处输入语义不同（character_arcs.name 是整名字符串，core_events.characters 是逗号分隔多角色字符串）。前移时需要保证两路都走同一函数。

### 4.2 边界条件

- `name = ""`（空字符串）：函数当前 `strip()` 后变 `""`，不会进入任何 `endswith` 分支，返回 `""`。建议前移时**显式跳过空名**而非塞进 KB。
- `name = "（主角）"`（纯后缀）：`base = ""`，函数当前返回 `"（主角）"`，是 bug。前移时必须修复。
- `name = "林凡（主角）（主角）"`（重复后缀）：函数当前只剥一次，返回 `"林凡（主角）"`。是否要循环剥？建议循环剥到稳定。
- `name = "（主角）林凡"`（前缀变体）：函数当前**不剥离**前缀变体，仅识别后缀。前移时**不要扩展**到前缀——会破坏与 `is_same_location` 的职责划分。

### 4.3 与其它模块的交互

- **KB trim 行为**（`memory_state._trim_kb`）：归一化后 key 合并可能让某些字段长度突破 trim 上限的概率变化（如"林凡"和"林凡（主角）"合并后少 1 个 key）——大概率是正向的。
- **持久化兼容性**：KB 持久化为 `knowledge.json`（KnowledgeBase.to_dict），归一化后字段值会变。旧 knowledge.json 加载时走 `KnowledgeBase.from_dict`（L69-115），直接读取 dict 值，**不重跑归一化**。所以：重启后 KB 不会自愈，除非显式重建。
- **断点续跑**：`memory_state.restore_from_disk`（L236-320）从 chapter_N_result.json 读回 `AnalysisResult`，name 字段是原始未归一化的。续跑时新章归一化（如果改进），旧章还是未归一化——KB 状态依旧分裂。

---

## 5. 兼容性影响

### 5.1 对断点续跑的影响

- 旧书的 `chapter_N_result.json`：name 字段已固化，前移**不会改磁盘**（除非显式重写）。
- 内存 KB 续跑：旧章 name 未归一化，新章 name 归一化（如果改进）→ 续跑完成后 KB 状态分裂（旧章带括号、新章裸名）。
- 解决方案：改进后必须提供"KB 重建工具"（rebuild from chapter_N_result.json with normalization），并在 changelog 中提示用户跑一次。

### 5.2 对旧书分析结果的影响

- `character_states`：旧"林凡"和"林凡（主角）"两条合并为一条"林凡"，最后写入的 surface_action 胜出。**可能丢失中间状态**（因为 seen_arcs 的去重顺序是章号序）。
- `character_relationships`：配对 key 合并后，desc 字段为最后写入的 `change_delta` 拼接。**可能丢失早期配对变化**。
- 实际影响程度：低到中。多数情况下"最后一次覆盖"语义符合用户预期，但**用户应当被告知** KB 会重算。

### 5.3 对 KB 数据结构的影响

- `character_states: Dict[str, str]`：key 合并，元素总数减少。
- `character_relationships: Dict[str, str]`：key 合并（去重）。
- 其它字段（`verified_facts`、`world_building` 等）不受影响。
- KnowledgeBase 字段定义 (`models/knowledge.py:13-32`) **无需修改**。

### 5.4 对伏笔账本状态机的影响

- 伏笔账本（`foreshadow_ledger.py`）只接收去重后的 catalog items，**不涉及 character 字段**。
- 无影响。

### 5.5 对 50 类分类体系的影响

- 50 类分类是 `foreshadowing.type` 字段，与 character 字段正交。
- 无影响。

---

## 6. 建议优先级

**优先级：中**。建议作为下一轮审计改进的**第一项**（4 分收益对应明确问题），但**必须配套 KB 重建脚本**与**迁移说明**（changelog 条目）才能落地。

理由：
- 收益明确（4 分）—— 修复一个正反馈污染循环
- 改动局部（2-3 分）—— 核心改动小
- 兼容性可控（4 分降到 3 分靠迁移工具）

---

## 7. 替代方案或简化版

### 7.1 最小变更版（推荐用于第一阶段）

**只把归一化函数从 `aggregate_utils.py` 提到 `text_utils.py` 公共位置**，不改任何合并点。理由：
- 立即让 `viz_service.py` / `character_graph.py` 等下游消费者能复用归一化函数
- 风险为零（纯重构）
- 实际效益：仍依赖下游消费者**主动调用**，所以收益有限

**评估**：收益 2 分，难度 1 分。作为第一步可接受。

### 7.2 渐进版（推荐作为主体改进）

分两步：
1. 把归一化函数提到 `text_utils.py:normalize_character_name(name) -> str`
2. 在 `memory_state._merge_result_into` 和 `knowledge_base._merge_analysis_results` / `_merge_incremental` 中**只对 `character_arcs` 路径**接入归一化（6 处合并点改 3 处，因为 arc 路径才用 `name`；core_events.characters 路径是字符串拆分，归一化要在拆分后做）
3. **不修改** `core_events.characters` 在 KB 中的处理（KB 不存 core_events.characters，仅存 character_states 和 character_relationships）—— 实际等价于只在 arc 路径归一化即可
4. 提供一次性 KB 重建脚本 `backend/cli/rebuild_kb_with_norm.py`

**评估**：收益 4 分，难度 2 分，兼容性 3 分（可由用户选择是否跑重建）。

### 7.3 激进版（不推荐）

直接修改 `AnalysisResult.from_dict` 在解析时归一化 `character_arcs[].name`。
- 优点：归一化提前到最早阶段，所有下游自然获益
- 缺点：破坏 "from_dict 是纯反序列化" 的现有契约（其它测试 `test_basic_roundtrip` 假设原样保留 name）；改动影响面最大
- 不推荐，除非有明确 P0 紧急性

---

## 8. 实施路径

### 阶段 0：准备（建议先做）

1. 在 `backend/tests/test_character_normalize.py` 新建测试文件，覆盖：
   - 空名、纯后缀、重复后缀、含中文/英文/全半角括号的各种 name 输入
   - `aggregate_character_tracking` 的归一化键值（用 fixture 验证 character_map 的 key）

### 阶段 1：函数迁移

2. 在 `backend/utils/text_utils.py` 新增：
   ```python
   def normalize_character_name(name: str) -> str:
       """归一化角色名：去除常见括号标注后缀。空名/纯后缀返回原值（向后兼容）。"""
       # 复用现有 _CHAR_NAME_SUFFIXES 列表
   ```
3. 在 `aggregate_utils.py` 中保留 `_CHAR_NAME_SUFFIXES` 与 `_normalize_char_name`（保持私有兼容），内部调用 `text_utils.normalize_character_name`

### 阶段 2：合并层接入

4. `memory_state.py:_merge_result_into`：
   - `kb.character_states[arc.name]` → `kb.character_states[normalize_character_name(arc.name)]`
   - `pair_key = f"{first.name}-{second.name}"` → `pair_key = f"{normalize_character_name(first.name)}-{normalize_character_name(second.name)}"`
   - 同步 `_extend_kb`、`_build_kb_from_results`、`memory_state._merge_one`
5. `knowledge_base.py`：
   - `_merge_analysis_results` (L366-378) 同步修改
   - `_merge_incremental` (L448-458) 同步修改

### 阶段 3：迁移工具

6. 新增 `backend/cli/rebuild_kb_after_normalize.py`（一次性脚本）：
   - 读 `chapter_N_result.json`
   - 走一遍 `_build_kb_from_results`（已含归一化）
   - 写回 `knowledge.json`
   - 备份旧 `knowledge.json` 到 `backups/`

### 阶段 4：测试

7. 扩 `test_optimizations.py` 或新建 `test_kb_normalize.py`：
   - 验证 KB 重建后 character_states 合并
   - 验证 character_relationships 配对 key 归一化
   - 验证并发补跑时 `in_order=False` 路径下归一化稳定性

### 阶段 5：文档

8. CHANGELOG 增加"角色名归一化前移"条目，提示用户对旧书跑重建脚本
9. AGENTS.md 增加"character name normalization"段落，说明规则与边界

**预计工时**：3-5 小时（含测试 + 文档），风险点集中在第 6 步的迁移脚本。
