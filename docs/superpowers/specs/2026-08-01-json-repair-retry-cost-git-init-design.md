# 设计：畸形 JSON 修复 + 重试成本可见 + git 初始化

日期：2026-08-01
状态：已获用户批准（2026-08-01）

## 背景

- 日志显示 MiniMax 偶发返回畸形 JSON（如 `{id":2` 键漏开引号、值以全角字符 `（`/`、` 开头且漏开引号），现有 7 层解析策略（direct → trim → 注释 → json5 → ast.literal_eval → json_repair → 单引号替换）均无法修复，只能靠降温重试兜底，每次重试都真实扣费。
- 重试失败烧掉的 token（`failed_tokens`）已在 llm_client 内统计，但未暴露给前端；前端统计面板在分析结束后不可见。
- 项目无 git 仓库，无法回滚；config.json 含 API Key，入库前必须排除。

## 一、畸形 JSON 修复（后端）

### json_utils.py

新增 `repair_missing_quotes(text: str) -> str`，仅当现有解析链全部失败后调用。两条正则（模式在合法 JSON 中不可能出现，安全）：

1. **键漏开引号**：`([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)"\s*:` → `\1"\2":`
   - 例：`{id":2` → `{"id":2`；`,"b":1}` 不受影响（`"b"` 前是 `"` 不匹配）
2. **值漏开引号**：`(:\s*)([^\s"{}[\],][^{}\[\]]*?)"(\s*[,}\]])` → `\1"\2"\3`
   - 例：`:"（内容）"` → `:"（内容）"`（修复 U+FF08/U+3001 类报错）；合法值（数字、`true`、已有开引号的字符串）均不匹配

`safe_parse_json` 末尾追加策略 8：`repair_missing_quotes` → 重试 `json.loads` → 重试 json5。仍失败返回 None。

### analyzer.py（重试提示增强）

- `validate_json_response` 中解析失败时返回带位置的信息：改用 `json_utils.parse_json_robust(text)`（新增），返回 `(dict|None, detail: str|None)`，detail 为最后一次 JSONDecodeError 的行/列（如 `Expecting property name enclosed in double quotes (line 1, col 45)`）及是否经修复后成功。
- `safe_parse_json` 保持签名不变（包装 `parse_json_robust` 只取 dict），供其他调用方使用。
- `build_retry_messages` 的提示升级为：「上次输出JSON解析失败：{detail}；出错位置附近原文：{snippet}」。snippet 通过闭包捕获的原始响应，按 detail 中的 line 定位取前后约 60 字符。
- 修复成功但必填字段校验失败时，detail 包含「已自动修复JSON，但缺少字段 X」。

### 测试

- 新增 `backend/tests/test_json_repair.py`：
  - 真实样本 1：`{"core_events":[...],"function":"..."},{id":2,...}` → 修复后正确解析
  - 真实样本 2：值漏开引号含 `、`（U+3001）→ 修复后正确解析
  - 真实样本 3：值漏开引号含 `（`（U+FF08）→ 修复后正确解析
  - 反例：合法 JSON 不被误伤（含嵌套对象、数组、Unicode、转义引号）

## 二、重试成本可见（后端 + 前端）

### llm_client.py

- 新增 `self._attempts = 0`，在 `chat()` 每次真实 API 调用时 +1（与 `_request_count` 同锁）。
- `get_stats()` 增加 `"total_attempts": self._attempts`。

### analyzer.py

- `analyze_chapter` 返回值改为 3 元组 `(result, ch_tokens, retry_info)`；retry_info 为 dict：
  `{"retries": int, "failed_tokens": int}`，通过调用 `chat_with_retry` 前后 `self.llm_client.get_stats()` 中 `total_attempts` 与 `failed_tokens` 的差值计算。
  - 成功时 retries = attempts 差值 - 1（第 1 次不算重试）
  - 失败返回 None 时同样带上差值（失败成本不可归零）

### pipeline.py

- `_analyze_one_block` 透传 retry_info；`_handle_block_outcome` 的 done/failed payload 增加
  `"retries": retry_info["retries"]`、`"failed_tokens": retry_info["failed_tokens"]`。

### queue_service.py

- `on_progress` 中 `_chapter_stats.append` 增加 `retries`、`failed_tokens` 字段；另维护
  `self._total_retries`、`self._total_failed_tokens` 累加（与 `_chapter_stats` 一样在 `start()` 时清零，每次运行独立统计）。
- `token_stats()` 响应增加 `total_retries`、`total_failed_tokens`。

### frontend StatsPage.vue

- 卡片区新增两卡：「重试次数」（amber）、「失败Tokens·已扣费」（red）。
- 每章明细表新增「重试」「失败Token」两列。
- 去掉「仅在运行中才取数」门控：始终调用 `/api/analysis/token_stats`，运行状态用「运行中/已结束」徽标展示（运行结束后保留本次完整统计）。

### 测试

- `backend/tests/test_llm_mock.py` 扩展：断言 `total_attempts` 随重试递增。
- `backend/tests/test_queue_service.py`：token_stats 含新字段。
- 前端 `npx vue-tsc --noEmit` 零错误。

## 三、git 初始化

- 根目录 `git init -b main`（当前无 .git）。
- `.gitignore` 内容（根目录）：
  ```
  config.json
  *.log
  workspace/
  __pycache__/
  *.pyc
  .pytest_cache/
  .venv/
  node_modules/
  frontend/dist/
  queue_state.json
  .env
  .tmp_verify/
  ```
- 仓库级身份（不动全局配置）：`user.name=novel-analyzer`、`user.email=dev@local`。
- 首次提交全部源码（排除项外），不推送远程。
- 验证：`git check-ignore config.json` 返回命中；`git status` 干净；`git log --oneline` 有 1 条提交。

## 范围外（YAGNI）

- 不做 LLM 提示词大改（仅重试提示增强）。
- 不做历史报告文件持久化（用户已选「面板+每章+结束后可见」）。
- 不引入远程仓库/CI。
- 不处理 330s 硬超时与上下文切块（另行评估）。

## 验证清单

1. `python -m pytest backend/tests -q` 全绿（旧 27 + 新测试）
2. `npx vue-tsc --noEmit` 零错误
3. `git check-ignore config.json` 命中
4. 手动：跑一轮分析，统计面板可见重试/失败 token；结束后仍可见
