# 审核报告 #1：动态上下文预算

> **审核对象**：在 `backend/core/prompt_builder.py:120-257` 的 `build_messages` 开头加"章节信号预检"（纯 Python，零 LLM 调用），按需注入 10 路上下文，预估省 20-40% user prompt token。
>
> **审核员**：Worker 子 agent
> **日期**：2026-08-27
> **审核模式**：纯只读源码 + 评估（不修改任何代码、不跑测试、不启动服务）

---

## 1. 源码调研摘要

### 1.1 已读文件

| 文件 | 关键内容 |
|---|---|
| `backend/core/prompt_builder.py`（320 行） | `build_messages` 主体（120-257 行）；`PromptBuilder.__init__` 全部 11 个 max_* 截断参数；`_render_structured_rolling` 静态方法（259-319 行）渲染 `rolling_structured` 6 个子字段 |
| `backend/core/analyzer.py`（249 行） | `validate_parsed_analysis`（25-46 行）JSON 校验；`analyze_chapter`（83-211 行）调用 `build_messages` + `chat_auto` + `build_retry_messages` |
| `backend/core/knowledge_base.py`（483 行） | `KnowledgeBaseManager.build_temp_knowledge`（123-263 行）含 _TEMP_KB_CACHE 缓存与增量合并；`_merge_analysis_results`（314-407 行）与 `_merge_incremental`（409-483 行）从 `AnalysisResult` 派生 KB 字段 |
| `backend/core/memory_state.py`（410+ 行） | `get_kb_snapshot`（91-139 行）含子集缓存（_snapshot_cache_limit/kb）；`_apply_rolling_to_snapshot`（141-153 行）注入 rolling_structured；`add_result`/`_merge_one` 乱序保护（328-341 行） |
| `backend/models/knowledge.py`（116 行） | `KnowledgeBase` dataclass 的全部 12+ 个字段定义（13-31 行） |
| `backend/config/settings.py`（365 行） | `AnalysisConfig` 全部 11 个 max_* 字段（148-203 行） |
| `backend/config/constants.py`（196 行） | 10 个 `DEFAULT_MAX_*_IN_PROMPT` 常量（18-23 行）；`MAX_*` 知识库硬上限（173-185 行） |
| `backend/api/routes_prompt.py`（121 行） | `POST /api/prompt/preview` 端点调用 `build_messages`（93 行） |
| `backend/tests/test_prompt_content_truncate.py`（31 行） | 唯一针对 `build_messages` 的测试，仅覆盖 `max_content_chars` 截断 |
| `backend/utils/aggregate_utils.py:286-364` | `aggregate_character_tracking`（**事后聚合**的"角色-章"映射，不可在 build_messages 阶段调用） |

### 1.2 关键发现

#### 1.2.1 KnowledgeBase 字段全清单（13 个持久化字段 + 1 个结构化滚动）

```python
@dataclass
class KnowledgeBase:
    story_timeline: str                              # 单值
    recent_summaries: List[str]                      # 前情摘要
    compressed_arcs: List[str]                       # 近期逐章 timeline
    character_states: Dict[str, str]                 # 角色名 → 表面行为
    verified_facts: List[str]
    long_term_arcs: List[str]
    character_relationships: Dict[str, str]         # "A-B" 配对 → 描述
    world_building: List[str]
    thematic_elements: List[str]
    foreshadowing_network: List[str]
    rolling_summary: str                             # 旧版兼容
    rolling_layer_early: str                         # 旧版兼容
    rolling_layer_recent: str                        # 旧版兼容
    rolling_layer_boundary: int                      # 旧版兼容
    rolling_structured: dict                         # 结构化 JSON：global_milestones / paradigm_layers / current_context / active_causal_chains / recent_momentum
```

#### 1.2.2 `build_messages` 中"10 路"静态注入的精确边界

| 注入块 | 字段源 | 截断逻辑 | 当前默认值 | 位置 |
|---|---|---|---|---|
| ① 伏笔分类表 | `FORESHADOW_CATEGORY_DEFS`（50 类固定文本） | 完整常量，无截断 | 全部 50 类 | system prompt:208-209 |
| ② 已知世界观 | `world_building` | `world[-max_world_items:]` | 20 条 | system prompt:211-212 |
| ③ 主题元素 | `thematic_elements` | `themes[-max_themes:]` | 8 条 | system prompt:214-215 |
| ④ 当前时间线 | `story_timeline` | `timeline[:timeline_truncate]` | 200 字符 | user prompt:227 |
| ⑤ 角色当前状态 | `character_states` | `items[-max_character_states:]` | 20 个角色 | user prompt:230 |
| ⑥ 角色关系 | `character_relationships` | `items[-max_relationships:]` | 15 对 | user prompt:233 |
| ⑦ 已验证事实 | `verified_facts` | `facts[-max_verified_facts:]` | 15 条 | user prompt:236 |
| ⑧ 前情摘要 | `recent_summaries` | `[-max_summaries:]` | 5 条 | user prompt:239 |
| ⑨ 伏笔网络 | `foreshadowing_network` | `[-max_foreshadow_entries:]` | 5 条 | user prompt:242 |
| ⑩ 完整故事历史 | `rolling_structured` + `compressed_arcs` | milestones/paradigms 不限；momentum 限 [-15:]；arcs 限 `[-max_arcs:]` | 30 条 | user prompt:245 |
| ⑪ 当前分析文本 | `chapter_content` | `content[-max_content_chars:]`，超窗标注丢弃字符数 | 30000 字符 | user prompt:248 |

> **注**：所谓"10 路"实际是 11 项（① 与 ②-③ 同在 system；④-⑪ 在 user）。`FORESHADOW_CATEGORY_TEXT` 是常量文本、实际无截断风险。

#### 1.2.3 `build_messages` 的 2 个调用点

| 调用方 | 上下文来源 | 用途 |
|---|---|---|
| `core/analyzer.py:110` | `MemoryState.get_kb_snapshot(chapter_limit=...)` | 真实分析主链路（concurrency 并发，KB 来自内存增量） |
| `api/routes_prompt.py:93` | `KnowledgeBaseManager.build_temp_knowledge(output_dir, chapter_limit=ch_idx-1)` | Prompt 预览端点（`POST /api/prompt/preview`） |

两处都传入 `KnowledgeBase`，**但** analyzer 路径的 KB 还可能注入 `rolling_structured`（经 `_apply_rolling_to_snapshot`），preview 路径则经 `_apply_rolling_data` 注入。两条路径的 KB 装配流程不同。

#### 1.2.4 KV cache 排序的隐含约束（关键）

prompt_builder.py:202-205 注释明确说"低频块放前面（prefix 稳定，提高远端 prompt cache 命中）"——`[已知世界观]` `[主题元素]` 放在 system 内（变化频率低），其余 6 路放在 user 内。如果做动态注入**重新洗牌 user prompt 块顺序**，会破坏现有 KV cache 布局，命中率可能下降。这是改进的隐性副作用。

#### 1.2.5 retry 链路对注入稳定性的依赖

`analyzer.py:136-144` `build_retry_messages`：复制 `current_messages` 后在 user 末尾追加格式修正 hint。如果动态注入基于 `chapter_content` 提取，retry 时 chapter_content 不变 → 提取结果稳定 → retry 行为不受影响。**但**如果动态注入依赖 KB 状态（如 `character_states` 大小），KB 在 retry 间隔被并发 worker 修改会改变重试的 prompt 内容，理论上重试与首次调用的 prompt 不一致。

#### 1.2.6 项目**无角色别名/归一化系统**（致命约束）

- `grep "alias|canonical"` 在 `backend/utils/` 零命中
- `aggregate_utils.py:308` 有 `_normalize_char_name` 函数，**但仅用于分析完成后的聚合阶段**，未在 KB 持久化字段
- 也就是说 `character_states` 的 key 是 LLM 自由产出的字符串（"小张"/"张铁柱"/"张哥"/"老张"/"张宗主"都可能出现），KB 里没有 alias 映射
- **结论**：做"本章出现的角色"判定只能用纯字符串包含判断，对网文常见别名/称呼场景漏检率高

---

## 2. 多角度评分

| 维度 | 评分 (1-5) | 理由 |
|---|---|---|
| **改进难度** | 2 | 核心逻辑容易写出（约 100-150 行：`_compute_chapter_signals()` 纯 Python + 配置项 + 测试）。**但**准确率上限受"角色无 alias 系统"硬约束，要么接受高漏检（按字符串包含），要么先做 alias 归一化（独立子项目，工作量翻倍）。 |
| **改进收益** | 2 | 量化：以 `max_character_states=20`、每条 30 字符计算，理论省约 200 token/章；总收益约 1020 token/章（用户声称 20-40%）。按 13 本书 × 1000 章 × ¥0.001/1k token 算 ≈ ¥13，远低于 1-2 天改代码+测试+回归的人力成本。**真实价值可能在给 M3 思考模型腾出 KV cache 余量**，但这个收益难以量化、且与"降低 max_* 默认值"（更简单方案）效果重叠。 |
| **代码复杂度提升** | 3 | 新增一个 `_compute_chapter_signals()` 静态方法；build_messages 改 7-8 个 if 块；增加 1-2 个 max_* 配置项。**中等**——不算复杂但让 prompt_builder 的"纯字符串拼装"职责被侵入。引入新概念"信号"和"按需过滤"，未来维护者需要理解两套规则。 |
| **维护难度** | 4 | 高风险点：(a) 字符串包含对中文分词不友好，"李寻欢"会同时命中"李寻"和"寻欢"两个伪命中；(b) 网文常见"群像章"（N 角色对话），按出现过滤几乎不缩量；(c) `character_relationships` 是 "A-B" 配对 key，过滤后关系网断裂（只剩 A-B 配对中两人都出现的）。一旦出 bug 排查路径长（哪个角色的状态没被注入？为什么？）。 |
| **兼容性风险** | 3 | (a) `build_messages` 是 P2 修复的"稳定锚点"（注释里反复强调 KV cache 友好），改动后缓存命中率可能下降；(b) PromptPreviewPage 依赖稳定的 user prompt 顺序调试用户配置，动态化后调试体验改变；(c) 已有 `test_prompt_content_truncate.py` 用 `KnowledgeBase()` 空 KB + 固定章节，动态注入需新增对应测试集。**不破坏数据格式**（KB 持久化不动），但破坏"分析可复现性"——同一 KB 在不同章节得到不同 prompt 文本。 |
| **测试覆盖成本** | 4 | 需要覆盖：(a) 群像章 vs 独角戏章 vs 纯过渡章三种信号模式；(b) 角色名带/不带别名的命中差异；(c) `foreshadowing_network` 在"本章无伏笔活动"时降到 2 条的边界；(d) KB 字段全空/部分空/全满三态；(e) 现有 retry 链路对动态 prompt 的兼容性。预估需要 6-10 个新测试用例 + 1 个完整章节回归测试集。**测试成本在 P1 级别**。 |
| **实施风险** | 4 | 关键路径 bug 模式：(1) 角色状态被错误截断 → LLM 误判"角色没变" → 漏报 delta；(2) 伏笔网络被截到 2 条 → 跨章呼应识别失败 → 违 `SYSTEM_PROMPT` 第 28 行的硬约束"找出跨章呼应、伏笔回收"；(3) 切到动态注入后 `chapter_limit` 子集构建的 KB 与"全量 KB" 在 `get_kb_snapshot` 缓存层的语义错位（已有 `_apply_rolling_to_snapshot` 例子：rolling_structured 此前因注入时机错位导致分析主链路拿到空 dict，见 memory_state.py:142-149 注释）。**风险等级与现有 2026-08-24 P1 修复同类**。 |

---

## 3. 关键发现

1. **KB 没有"角色-章"反向索引**：`aggregate_character_tracking`（aggregate_utils.py:286）只在所有章节分析完成后才生成 `character_appearances`，**build_messages 阶段拿不到**。要实现"按章出现过滤"必须在 build_messages 时现扫 chapter_content。

2. **无 alias 系统 → 纯字符串包含是唯一手段**：
   - `character_states` 的 key 是 LLM 自由产出
   - 网文中"小张" / "张铁柱" / "张哥" / "张宗主" 多个名字指向同一角色
   - 字符串 `if name in chapter_content` 会漏掉"用别名提到"的角色，导致本章没提到的角色被错误保留

3. **`character_relationships` 是 "A-B" 配对 key**（knowledge_base.py:376-378）：
   - 单边过滤会破坏配对完整性（A 出现但 B 没出现 → 关系信息丢失）
   - 当前实现按章追加会重复覆盖（每次 A 和 B 同章出现，pair_key 描述被刷新为"新 delta"）—— 这反而是好事，因为 latest 一对就是当前关系

4. **`rolling_structured` 不应截断**：其中 `current_context` / `active_causal_chains` / `global_milestones` 是"当前故事进度"，**是主线摘要的核心**，不能"按伏笔活动"截断。提案者将伏笔网络截到 2 条合理（仅影响次要呼应），但混淆了 rolling_structured 字段与 foreshadowing_network 字段。

5. **KB 派生是单向的**：从 `AnalysisResult` 派生到 `KnowledgeBase`（knowledge_base.py:314-407），**没有反向流**。LLM 在 `character_arcs` 输出的"本章角色"信息在 `result.to_dict()` 之后才可用，但 `build_messages` 时拿不到当前章节的 result（结果在 LLM 调用后才有）。**这是根本性的时间错位**——改进的"按本章出现过滤"逻辑只能基于"本章全文"启发式扫描。

6. **Prompt 预览端点 vs 真实分析端点的注入差异**：
   - preview 用 `KnowledgeBaseManager.build_temp_knowledge`（无 rolling_structured 实时更新）
   - analysis 用 `MemoryState.get_kb_snapshot`（含 rolling_structured 实时注入）
   - 动态注入逻辑必须在两处都加，且保证一致性——这扩大了改动面

7. **MAX_THEMATIC_ELEMENTS = 100 但 prompt 默认 max_themes = 8**（constants.py:181 vs prompt_builder.py:102）：KB 上限比注入上限大 12.5 倍，动态注入几乎没有"按需裁剪"空间（已经裁到 8 条）。

8. **SYSTEM_PROMPT 已有"已知元素的深化 vs 全新元素的引入"区分规则**（prompt_builder.py:24）：LLM 被训练要区分"已有"和"新有"，但当动态注入 0 条 character_states 时，LLM 不知道"哪些是已知角色"——这反而违反现有 SYSTEM_PROMPT 规则。

---

## 4. 实施风险

### 4.1 边界条件会出问题的场景

| 场景 | 风险 | 严重性 |
|---|---|---|
| 群像章（5+ 角色对话） | 字符串扫描会命中几乎所有角色，**几乎不缩量**——改进形同虚设 | 中 |
| 角色用别名/称呼 | "张哥"、"张宗主"不会命中"张铁柱"，导致 KB 角色被错误剔除 | 高 |
| 第 1 章（KB 全空） | `_compute_chapter_signals` 返回空集，所有按需过滤退化为不注入——但此时 LLM 没有任何上下文，是否更糟？ | 中 |
| KB 角色数 < max_*（如 5 个角色，max=20） | 动态过滤会进一步减到 2-3 个，LLM 看不到完整角色阵容 | 高 |
| 跨章"角色延续"（第 N 章提到第 N-3 章的角色但该角色已不活跃） | 字符串扫描命中 → 状态被注入（正确）；但如果该角色在 N 章只被一笔带过，注入 30 字状态 = 浪费 token | 低 |
| `foreshadowing_network` 在"本章无伏笔活动"被截到 2 条 | 但 [完整故事历史] 里的 rolling_structured 仍含伏笔因果链 → 两条线索来源不一致，LLM 困惑 | 中 |
| 章节含元数据/广告/水印（如 splitter_service.py:131 的公众号/书友群） | 字符串扫描会误命中"扫描到的清理残留字符串"（已删但 mtime 内可能短暂存在） | 低 |
| chapter_content 超 max_content_chars 30000 被截断 | 截断后扫描的是"后段"，前段提到的角色被错误过滤 | 高 |
| 角色名是单字（如"刀"）或短词（如"黑子"） | 字符串包含会高频误命中 | 中 |

### 4.2 哪些 prompt 规则依赖完整上下文

- **SYSTEM_PROMPT 第 28 行**："特别注意：如果角色在[角色当前状态]中的描述与本章行为一致，说明角色尚未变化"——这一规则**假设**角色状态字段是完整的。动态注入把未出现角色的状态全部删掉，LLM 看到的状态字段不再"完整"——它无法区分"未变化"还是"未注入"。
- **SYSTEM_PROMPT 第 24 行**："区分'已知元素的深化'和'全新元素的引入'"——同上，需要完整 worldview。
- **SYSTEM_PROMPT 第 26 行**："判断本章是否回收了旧伏笔或触发了伏笔链条"——只注入 2 条伏笔会显著降低跨章呼应的召回率。
- **Output schema `core_events[].function` 和 `importance`**：依赖 LLM 看到"全书的角色网和事件线"才能判断重要性。动态过滤后 LLM 失去全局视角。

### 4.3 不可见的副作用

- **KV cache 命中率下降**：现有 user prompt 内 6 块顺序固定，动态注入会改变块的相对顺序。Anthropic prompt cache 对 prefix 顺序敏感（前缀变了 cache 失效）。
- **断点续跑的等价性**：重跑同一章节时，KB 一致 → 动态注入结果一致 → 输出可复现（这反而是好事）。**但**如果在重跑之间并发分析了新章节（更新了 rolling_structured），相同章节的 prompt 内容会变。
- **测试时序依赖**：单元测试用空 KB 写的，用"充实 KB"复现就过不了。

---

## 5. 兼容性影响

### 5.1 对断点续跑的影响
- **低风险**：`build_messages` 是无状态纯函数；输入（KB + chapter_content）一致 → 输出稳定
- **中风险**：dynamic 注入依赖 chapter_content，而 chapter_content 在断点续跑时**必须**从原始文件读取（不依赖 KB），所以"按章过滤"逻辑在续跑时与首次跑一致
- **隐含风险**：现有 `_apply_rolling_to_snapshot` 是 P1 修复（2026-08-24 审计），rolling_structured 注入时机错位曾导致主链路拿到空 dict——dynamic 注入**又一次**依赖 KB 状态正确装配，必须保证两路径同步

### 5.2 对 KB 快照的影响
- **零影响**：dynamic 注入发生在 build_messages 内部，不改 KB 字段、不改快照语义
- **潜在收益**：dynamic 注入可能让 prompt token 减少，间接让 LLM 响应更快/更准

### 5.3 对测试套件的影响
- **必须更新**：`test_prompt_content_truncate.py` 现有 2 个测试都假设 KB 是空 KnowledgeBase()；dynamic 注入后即使是空 KB 也要走"全空信号"分支，需新增对应测试
- **必须新增**：
  - 群像章 vs 独角戏章 vs 过渡章三种信号测试
  - `foreshadowing_network` 截断边界测试
  - `character_relationships` 配对完整性测试
  - 角色别名/同义词的命中差异测试
- **回归风险**：dynamic 注入会改变所有现有 7 块 user prompt 的截取逻辑，需对完整章节做 A/B 回归测试（成本高）

### 5.4 对前端 PromptPreviewPage 的影响
- **直接破坏**：`PromptPreviewPage` 调 `POST /api/prompt/preview` 返回的 `params.kb_arcs / kb_summaries` 等是全局值；dynamic 注入后实际注入到 prompt 的内容**不等于这些参数**——前端展示与实际脱节
- **用户调试困惑**：用户调大 `max_character_states=30` 但某章只看到 5 个角色，会以为有 bug
- **必须前端同步改造**：要么前端加"按章过滤后实际注入数"字段，要么在 `params` 里返回"本章动态模式"标记

### 5.5 对 SYSTEM_PROMPT 规则的影响
- **必须同步修改 SYSTEM_PROMPT**（或新增 1 条规则说明"本章未注入的 X 字段不代表 X 不存在"），否则 LLM 看到空字段会误判

---

## 6. 建议优先级

### 6.1 优先级判定

| 选项 | 优先级 | 理由 |
|---|---|---|
| **完整实现（按提案原意）** | **P3** | 收益低（¥13/13 本书）、成本高（150 行新代码 + 6-10 个测试 + 端到端回归）、引入新维护负担、对网文别名场景准确率低、有 SYSTEM_PROMPT 同步修改需求。**不建议做**。 |
| **简化版：仅 character_states 按章过滤** | **P2** | 最大单点收益（约 200 token/章）、字典结构最适合查、改动最小（约 30 行）。但仍有别名风险。**可做试点**。 |
| **更简化版：直接降低 max_* 默认值** | **P1** | 改 5 个常量值 + 1 段注释，约 10 行变更，零回归风险。理论省 token 与 dynamic 注入重叠 50-70%。**强烈推荐先做**。 |
| **不实施** | **P3** | 综合 ROI 太低。**默认建议**。 |

### 6.2 推荐路径

1. **先做"降默认值"（P1）**：`max_character_states` 20→10、`max_relationships` 15→8、`max_verified_facts` 15→8、`max_world_items` 20→10、`max_foreshadow_entries` 5→3。一行配置改动，零测试回归。
2. **观察 1-2 周**：用 1 本书跑全流程，对比分析质量（核心字段完整度、跨章呼应识别率）。如果发现"群像章 token 浪费严重"再考虑 P2 方案。
3. **P2 试点 character_states 按章过滤**：仅对 `character_states` 字典做"按章出现"过滤，复用 `rolling_structured.current_context.location` 作为"本章焦点角色"（无需扫描正文，准确率比纯字符串高）。
4. **不实施完整方案**（P3 提案）。

---

## 7. 替代方案或简化版

### 7.1 最低可行版本（MVP，约 30 行）

只对 `character_states` 做动态过滤，复用 `rolling_structured.current_context.location` 信息定位"本章焦点角色"：

```python
def _filter_character_states(self, kb: KnowledgeBase) -> dict:
    """MVP：仅过滤 character_states，焦点信息从 rolling_structured 拿"""
    cs = kb.character_states
    if not cs:
        return {}
    # 从 rolling_structured 拿当前焦点
    ctx = (kb.rolling_structured or {}).get("current_context", {})
    focus = ctx.get("location", "")  # 弱信号：location 不直接是角色名
    # 退路：若 rolling 没有当前焦点信息，返回全量（不缩）
    if not focus:
        return dict(list(cs.items())[-self.max_character_states:])
    # 按 "焦点 location 是否在 character_states 的 value 里" 模糊匹配
    return {k: v for k, v in cs.items() if k in focus or focus in v}
```

**问题**：location 是地点不是角色名——这个 MVP 在结构上不成立。**真正可行的 MVP 必须是字符串扫描 chapter_content**。

### 7.2 真正可用的 MVP（字符串扫描，约 60 行）

```python
def _filter_by_presence(self, items: list, chapter_content: str,
                        key_extractor) -> list:
    """items: 列表或 items() 列表；key_extractor: 从 item 提字符串的函数"""
    if not items or not chapter_content:
        return items
    keep = [it for it in items if key_extractor(it) in chapter_content]
    return keep if keep else items  # 全部不命中时退化为全量（不缩）
```

**问题**：见 §4.1 边界条件表。

### 7.3 替代方案：双轨制

不动 `build_messages`，新增 `_build_messages_compact()` 变体，**仅 PromptPreviewPage 用**。真实分析路径保持 10 路全量注入以保护 KV cache 命中率和 LLM 上下文完整性。优点：用户调试体验更优（看到"精简后"prompt）；缺点：分析路径和预览路径结果不一致，调试时困惑。

### 7.4 替代方案：延迟注入（Lazy Injection）

不动 prompt 大小，改 `chapter_content` 的截断逻辑——把 max_content_chars 从 30000 提到 50000，给 1.5x token 余量，让"长章"不再被截断。约 5 行改动，**实际收益可能比 dynamic 注入高**（超窗是确定性失败）。

---

## 8. 实施路径（若决定实施完整方案）

按依赖顺序列关键步骤，每步独立可回滚：

### 8.1 准备阶段
1. **新增配置项**（constants.py + settings.py + PromptBuilder.__init__）：
   - `enable_dynamic_context: bool = False`（默认关，作为功能开关）
   - `min_char_states_for_filter: int = 10`（KB 角色数 < 此值不缩）
2. **新增日志埋点**：在 build_messages 记录 `kb_total_chars / dynamic_kept_chars / dynamic_saved_ratio` 供回归对比

### 8.2 实现阶段
3. **新增 `_compute_chapter_signals(chapter_content, kb)`**：
   - 扫描 chapter_content 提取出现的角色名（纯字符串包含）
   - 检测本章是否有伏笔活动（heuristic：keyword 匹配 "伏"、"暗示"、"谜"、"线索" 或 LLM 给的 [伏笔网络] 长度变化）
4. **改造 `build_messages`**：
   - 在 159-200 行（各路 ctx 计算）每路后加 if `enable_dynamic_context and signal_x: keep = filter(keep)` 包装
   - 8 处条件分支，每处 2-3 行
5. **同步改造 `routes_prompt.py`**：传 `enable_dynamic_context` 给 PromptBuilder

### 8.3 测试阶段
6. **新增单元测试**（~6 个）：
   - 空 KB + 长章 → 全部退化为"无信号"
   - 充实 KB + 群像章 → 信号集接近 KB 全量
   - 充实 KB + 独角戏章 → 缩到 1-2 个角色
   - 角色名带数字/特殊符号
   - `foreshadowing_network` 截断边界（5 → 2）
   - rolling_structured 注入顺序对 dynamic 过滤的影响
7. **回归测试**：选 1 本已分析过的书（如《盗墓笔记》前 30 章），用 dynamic on/off 各跑一遍，对比 LLM 输出 JSON 的 `core_events` 完整度、跨章呼应引用率

### 8.4 上线阶段
8. **默认关，按书配置**：在前端设置页加"动态上下文预算"开关，默认 false；用户主动开
9. **观察期**：1-2 周内收集"分析质量下降"反馈
10. **回滚预案**：feature flag 一行可关

### 8.5 估算工作量
- 代码：~150 行
- 测试：~6 个新测试 + 1 套回归
- 前端：~20 行（设置页加开关）
- 文档：~50 行（用户说明）
- **总计：3-5 人天**（含回归）

---

## 9. 综合结论

**不建议完整实施**。理由：

1. **收益低**：约 ¥13/13 本书的 token 节省，与 3-5 人天改造成本严重不匹配
2. **准确率硬约束**：无角色别名系统，纯字符串包含在网文场景漏检率高
3. **隐性破坏大**：KV cache 命中率下降、SYSTEM_PROMPT 规则同步修改、PromptPreviewPage 用户体验改变
4. **更优替代存在**：降低 max_* 默认值（一行改动，零回归）已能覆盖 50-70% 收益

**若仍要实施**：仅做 `character_states` 按章过滤的 MVP（§7.1/§7.2），作为可逆实验。其他路（relationships/verified_facts/foreshadowing_network）保持原样。

**若不实施**：直接做 §6.2 第 1 步的"降默认值"——一行配置改动，零风险，至少拿回一半收益。

---

## 附录：调研溯源

- **build_messages 调用点**：`core/analyzer.py:110`、`api/routes_prompt.py:93`
- **build_messages 测试**：`tests/test_prompt_content_truncate.py`（2 个测试，仅覆盖 max_content_chars）
- **rolling_structured 使用**：`models/knowledge.py:31, 41-110`、`core/knowledge_base.py:118-121`、`core/memory_state.py:141-153`、`core/prompt_builder.py:140`、`core/pipeline.py:815-1075`
- **角色-章映射（事后）**：`utils/aggregate_utils.py:286-364` `aggregate_character_tracking`
- **角色别名归一化（仅聚合阶段）**：`utils/aggregate_utils.py:308` `_normalize_char_name`
- **已审计的同类 P1 修复**：`memory_state.py:142-149`（rolling_structured 注入时机错位；2026-08-24 审计）
