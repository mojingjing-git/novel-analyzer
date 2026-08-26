# 分析队列左面板「运行中」实时仪表盘（H17 / S1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **状态**：草案（2026-08-26），等用户最终批准后执行。
> **依赖**：Phase 1-2 无依赖可立即做；Phase 3 依赖 H16 Phase 3（token_delta 接入 analyzer/pipeline）完成。
> **分支**：在 `h16-recovery` 上继续（`feature/streaming-tokens` 名字被并发 watcher 针对性删除，见 2026-08-26 事故记录；改名等 watcher 停止后再议）。

## Goal

分析运行期间，QueuePage 左侧面板（现为 ChapterDetailPanel，运行中无数据可显）改为**实时仪表盘**：聚合指标、并发车道视图、新发现流三个区块。解决"跑起来后左面板空白、用户不知道模型在不在干活"的体验问题。

**信息形态设计原则**：并发下凡"替换式"显示必闪烁；本方案只选**单调递增**（聚合计数）与 **append-only**（发现流）形态，车道视图按块固定绑定不互相覆盖。

**不做**（明确范围外）：
- 不做流式内容（content_delta）输出——观感差，已否决
- 不做跨块伏笔去重/「伏笔推进至第 N 次提及」（需模糊匹配，数据支撑不足，诚实不做）
- 不动 ChapterDetailPanel 现有功能（tab 共存，不替换）
- 不动 prompt / 分析逻辑 / checkpoint

## 事实核查结论（2026-08-26，已验证代码）

审核初版提案时发现的关键事实修正：

| # | 初版假设 | 实际情况 | 影响 |
|---|---|---|---|
| 1 | `block_done` 已携带完整 result，前端可零后端改动派生发现流 | `queue_service.py::on_progress`（约 700-720 行）转发时**剥离了 result**，只发 `chapter/ok/elapsed/tokens` 四字段 | 方案 A（纯前端）不成立，需小后端增量 |
| 2 | 车道开始时间可从现有消息推导 | pipeline 有 `status:"start"` 事件（pipeline.py:657），但 on_progress 只把它转成 log 文本，**无结构化 WS 消息** | 需 2 行转发改动 |
| 3 | 聚合 token 指标可直接用 H16 token_delta | H16 仅完成 Phase 1（骨架），业务链路未接入 | V1 降级用现有 5s 轮询 `getSessionTokenStats` |
| 4 | QueuePage 已规划 LiveTokenCounter（H16 Task 3.5） | 与本 plan 聚合卡重叠 | **合并决策：本 plan Phase 3 聚合卡替代 QueuePage 的 LiveTokenCounter**；SummaryPage 四阶段卡保持 H16 原计划不变 |
| 5 | block_done 的 chapter 是章号 | 是 block_id（块起始章号）；真实章范围在 message 文本里 | 车道/发现流显示章范围需后端补 `range` 字段或前端格式化 |

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│ QueuePage 左面板（tab：运行概览 | 章节详情）                │
│                                                            │
│ ┌────────────────────────────────────────────────────┐    │
│ │ ① 聚合指标行（V1：轮询 token_stats + progress ETA） │    │
│ │    V2（H16 后）：tokens 累计 / 聚合速率 / sparkline │    │
│ └────────────────────────────────────────────────────┘    │
│ ┌────────────────────────────────────────────────────┐    │
│ │ ② 并发车道（N = concurrency，动态）                │    │
│ │    block_start/block_done 驱动：章范围·耗时·状态   │    │
│ │    V2：+进度条（token估算/历史均值）+卡死预警       │    │
│ └────────────────────────────────────────────────────┘    │
│ ┌────────────────────────────────────────────────────┐    │
│ │ ③ 新发现流（append-only，ring buffer 200 条）      │    │
│ │    discovery 消息：核心事件数·新伏笔·新人物·悬念   │    │
│ └────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────┘
        ▲ WS
┌───────┴────────────────────────────────────────────────────┐
│ queue_service.on_progress（本 plan Phase 1 改造点）         │
│  status=start → {"type":"block_start", payload:{...}}      │
│  status=done  → block_done（补 range 字段）                │
│                → {"type":"discovery", payload:{...}} ←新增 │
│                   从 payload["result"] 提取摘要            │
└────────────────────────────────────────────────────────────┘
```

## Tech Stack

- 后端：仅改 `backend/services/queue_service.py` 的 on_progress 转发层（约 40 行）
- 前端：Vue 3.5 + TS；新组件 `LaneView.vue` / `DiscoveryFeed.vue` / `RunDashboard.vue`；**不引入 d3-shape / @vueuse**（sparkline 用 30 点 polyline 手写，Phase 3 再说）
- 测试：pytest 补 queue_service 转发用例；vitest 补组件用例（同目录 `.test.ts`，遵循项目惯例）

## Global Constraints（继承 agent.md §9）

- 禁止打印/提交 config.json；测试用 tmp_path
- 不批量转换行尾符；改动验证用 `git diff -w`
- 每任务落地后：后端 `python -m pytest`、前端 `cd frontend && npm run build`
- 同步更新 agent.md（Phase 4 收口）
- **多 Agent 并发警告**：本仓库当前有其他会话在跑（_THINKING_DETECTORS 探测器改动未提交，watcher 事件未平息）。提交前 `git status` 核对，**只 add 本 plan 涉及的文件**；分支用 `h16-recovery`
- commit 信息中文一行式

## Design Decisions（6 个关键决策）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| 1 | 发现流的"新"判定在哪做 | **后端（方案 B）** | 事实修正①：result 在转发层已被剥离；且后端判定跨会话/刷新不丢失，前端零解析逻辑 |
| 2 | 左面板与 ChapterDetailPanel 关系 | **tab 共存** | 运行中默认「运行概览」，完成后默认「章节详情」；手动切换后本会话不再自动切 |
| 3 | 车道 = 什么 | **并发槽位 lane** | 块开始→占一条空 lane→完成/失败→释放；lane 数 = concurrency 动态渲染；块与 lane 绑定后不被其它块覆盖，这是不闪的关键 |
| 4 | 卡死预警阈值 | **可配置，默认 8 分钟**（V2） | M3 思考模型 5-6 分钟正常生成 vs 真卡死难分辨；阈值进 config，超时 lane 变红但不自动干预 |
| 5 | 发现流条目来源 | **4 类**：块完成摘要 / 新人物首次登场 / 新伏笔（本块内线索，不跨块去重）/ 遗留悬念（cross_block.unresolved_questions 截断 24 字） | 全部来自 result 已有字段，零额外 LLM 成本；跨块伏笔追踪明确不做 |
| 6 | V1/V2 分期 | **V1 无 H16 依赖立即可做** | 车道只显耗时+状态、聚合用现有轮询；进度条/速率/sparkline 等 H16 token_delta 接入后升级 V2 |

## Phase 1：后端转发层增量（0.5 天）

### Task 1.1: block_start 转发 + block_done 补 range

**Files:**
- Modify: `backend/services/queue_service.py`（on_progress，约 700-720 行）

```python
if status == "start":
    await hub.publish({
        "type": "block_start",
        "payload": {
            "chapter": payload.get("chapter", 0),      # block_id
            "range": message,                           # 复用现成的 "开始分析第X-Y章..." 文本
            "progress": payload.get("progress", 0),
            "total": payload.get("total", 0),
            "ts": time.time(),
        },
    })
```

block_done payload 增补 `"range": message`（失败/完成 message 里有 ch_range）。

- [ ] 实现 block_start 转发 + block_done 补 range
- [ ] 单测：`backend/tests/test_queue_service.py` 补 2 用例（start 转发、done 带 range）
- [ ] 前端 `useProgressSocket.ts` 的 ProgressMessage type 联合加 `'block_start' | 'discovery'`

### Task 1.2: discovery 消息（方案 B：转发层提取摘要）

**Files:**
- Modify: `backend/services/queue_service.py`（on_progress done 分支内）

```python
def _extract_discovery(result: Optional[dict]) -> Optional[dict]:
    """从块结果提取发现流摘要（纯函数，好测）"""
    if not isinstance(result, dict):
        return None
    return {
        "events": len(result.get("core_events", [])),
        "foreshadows": [str(f.get("clue", ""))[:24] for f in result.get("foreshadowing", [])][:5],
        "characters": _extract_new_characters(result),   # 停用词过滤 + 首次登场判定
        "unresolved": [str(q)[:24] for q in (result.get("cross_block", {}) or {}).get("unresolved_questions", [])][:2],
    }
```

- `characters` 判定：`core_events[].characters` 逗号分隔拆分、trim、长度 2-8、停用词表（众人/他们/对方/旁白/自己）；**首次登场**由类级 `self._seen_characters: set` 维护（queue 任务生命周期内持久，断点续跑时从已完成块的 result 重建可后置为 Open Question）
- status=done 且 result 存在时：`hub.publish({"type": "discovery", "payload": {...}})`
- [ ] 实现 `_extract_discovery` + 已见人物集合
- [ ] 单测 3 用例：正常提取、停用词过滤、人物去重
- [ ] 验收：现有测试 0 回归

## Phase 2：前端 V1 仪表盘（1 天）

### Task 2.1: 左面板 tab 化

**Files:**
- Modify: `frontend/src/pages/QueuePage.vue`（split-col 区域）
- Create: `frontend/src/components/RunDashboard.vue`

- tab 头：「运行概览」「章节详情」；`status?.running` 且未手动切换时默认运行概览，否则章节详情
- [ ] tab 切换 + 默认值逻辑
- [ ] 手动切换后本会话记忆（组件内 ref 即可）

### Task 2.2: LaneView 车道组件

**Files:**
- Create: `frontend/src/components/LaneView.vue`

状态机：`block_start` → 找空 lane 占位（记 ts）→ `block_done`（chapter 匹配）→ 释放。lane 上限 = `status?.concurrency`（无则默认 4）。

每条 lane 显示：章范围文本、已耗时（1s 本地 tick）、完成态（绿）/失败态（红）。V1 无进度条。

- [ ] LaneView 状态机 + 渲染
- [ ] `LaneView.test.ts`：占位/释放/失败态/并发超 lane 上限
- [ ] 1s tick 用 setInterval，onUnmounted 清理

### Task 2.3: DiscoveryFeed 组件

**Files:**
- Create: `frontend/src/components/DiscoveryFeed.vue`

- 消费 `discovery` 消息，append-only，ring buffer 200 条
- 条目样式：时间戳 + 一行摘要；新伏笔/新人物紫色高亮（`#534AB7`）
- 自动滚底，hover 暂停滚动
- [ ] DiscoveryFeed 渲染 + ring buffer + 自动滚动
- [ ] `DiscoveryFeed.test.ts`：追加/上限裁剪/hover 暂停

### Task 2.4: 聚合指标行（V1 版）

**Files:**
- Modify: `frontend/src/components/RunDashboard.vue`

V1 数据源：现有 `api.getTokenStats()`（QueuePage 已有 refreshTokens 轮询，复用）+ `progress` 消息的 current/total/eta。四张卡：已完成 x/y、ETA、本会话输入 tokens、本会话输出 tokens。数字动画复用现有 `CountUp.vue`。

- [ ] 四卡聚合行
- [ ] `npm run build` + `vue-tsc --noEmit` 0 错

### Phase 2 验收

- [ ] 真实跑一本书（或 mock WS）：车道随块起止增减、发现流持续追加、无闪烁
- [ ] 现有前端测试 0 回归，新增 ≥10 用例全过

## Phase 3：H16 集成升级 V2（0.5 天，依赖 H16 Phase 3）

### Task 3.1: 车道进度条 + 卡死预警

- `token_delta`（context="analysis", channel 按块分 key）驱动车道进度条：估算 token / 该书历史均值（前 N 块 completion_tokens 均值，冷启动用全库默认 3k）
- 超过阈值（config，默认 480s）无增量的 lane 变红 + ⚠
- [ ] 进度条 + 预警
- [ ] config 加 `stall_warn_sec` 字段（agent.md §9.5 不破坏业务：仅视觉警告不干预）

### Task 3.2: 聚合卡升级

- tokens 累计改为 token_delta 聚合值（多块求和），速率 tok/s + 手写 polyline sparkline（30 点，不引 d3）
- **替代 H16 Task 3.5 的 QueuePage LiveTokenCounter**（SummaryPage 部分保持 H16 原计划）

- [ ] 聚合升级 + sparkline
- [ ] 更新 H16 plan 文档中 Task 3.5 的范围说明（加一行交叉引用）

## Phase 4：测试 + 文档 + 提交（0.5 天）

- [ ] 后端新增 ≥5 用例、前端新增 ≥10 用例全过；全量 0 回归
- [ ] agent.md §10.20 加 H17 条目（根因/方案/数据/收益）
- [ ] CHANGELOG.md 加 `## 2026-08-26 H17 队列运行中实时仪表盘`
- [ ] README.md「实时进度」章节更新
- [ ] `git add` 仅本 plan 文件清单（**严格不混入 _THINKING_DETECTORS 等其他会话未提交改动**）；commit 中文一行式；提交前 `git status` 双核对

## Risk Matrix

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| 1 | 其他会话 watcher 仍在删分支/并发 git 操作 | 中 | 高 | 全程在 h16-recovery 上；每次 commit 前 git status 核对；发现异常先 `git branch <备份名> <sha>` |
| 2 | discovery 人物首次登场判定在续跑后全量"首次" | 高 | 低 | V1 接受（标注于 UI tooltip）；重建逻辑列为 Open Question |
| 3 | 车道 1s tick × N lane 的渲染开销 | 低 | 低 | 纯文本耗时更新，Vue 静态提升足够；必要时 2s tick |
| 4 | block_start 与 block_done 的 chapter 匹配错位（重试块复用 block_id） | 中 | 中 | lane 匹配 key = block_id + start 序号；重试同 id 先释放旧 lane 再占位 |
| 5 | 停用词表覆盖不足，"新人物"出噪音 | 中 | 低 | 上线后从 api_failures/用户反馈迭代词表；词表独立常量好改 |
| 6 | H16 Phase 3 延期导致 Phase 3 悬空 | 中 | 低 | V1 独立交付不受影响；Phase 3 单独排期 |

## Acceptance Criteria（全局）

1. 分析运行中左面板不再空白：车道 + 发现流 + 聚合计数实时更新
2. 并发多块时数字/车道不闪烁、不互相覆盖（信息形态原则落地）
3. V1 不依赖 H16 独立可用；V2 在 H16 Phase 3 后升级
4. 断点续跑、停止、审核跳过（skipped）等路径 lane 状态机不残留脏 lane
5. 现有测试 0 回归，新增 ≥15 用例
6. agent.md / CHANGELOG / README 同步更新

## Test Plan

### 单元测试
- `backend/tests/test_queue_service.py`（+5）：block_start 转发、block_done 带 range、discovery 提取（正常/停用词/人物去重）
- `frontend/src/components/LaneView.test.ts`（5）：占位、释放、失败态、超上限、重试复用 block_id
- `frontend/src/components/DiscoveryFeed.test.ts`（4）：追加、上限裁剪、hover 暂停、高亮渲染
- `frontend/src/components/RunDashboard.test.ts`（2）：tab 默认值、聚合卡渲染

### 真实环境
- 跑 1 本书 ≥20 章：观察车道起止与发现流；中途停止 → 无脏 lane；刷新页面 → tab 回到正确默认

## Open Questions（等用户确认）

1. 发现流要不要持久化（刷新后回放）？V1 建议：不持久化，刷新清空
2. 续跑后人物"首次登场"集合要不要从已完成块重建？（成本：启动时扫全部 chapter result）
3. `stall_warn_sec` 默认 480s 是否合适（M3 思考模型 5-6 分钟正常生成）？
4. tab 记忆要不要跨会话（localStorage）？
5. Phase 1/2 是否交给 minimax 会话执行（它跑 H16 主线）？本仓库多会话并发 git 的协调方式

## Timeline

- Phase 1（后端转发层）：0.5 天
- Phase 2（前端 V1）：1 天
- Phase 3（H16 集成 V2）：0.5 天（依赖 H16 Phase 3）
- Phase 4（测试文档提交）：0.5 天
- 总计：2.5 天（V1 部分 1.5 天可先行交付）

## References

- 初版提案讨论：2026-08-26 会话（聚合指标/车道/发现流三区块草图）
- 转发层现状：`backend/services/queue_service.py::on_progress`（约 700-720 行）
- pipeline 事件源：`backend/core/pipeline.py` `_emit` status=start（:657）/done/failed/skipped（:698-731）
- result 结构：`backend/models/analysis_result.py`（core_events[].characters 逗号分隔 / foreshadowing[].clue / cross_block.unresolved_questions）
- H16 plan：`docs/superpowers/plans/2026-08-26-streaming-tokens.md`（本 plan Phase 3 与其 Task 3.5 有合并关系）
- 前端 WS 单例：`frontend/src/api/useProgressSocket.ts`（type 联合需扩）
