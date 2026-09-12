# API 参考

> 完整 API 也可看运行后的 `/docs`（Swagger UI）。所有 REST 端点前缀 `/api`。

## 分析

| 端点 | 方法 | 用途 |
|---|---|---|
| `/analysis/start` | POST | 启动队列分析（带配置覆盖） |
| `/analysis/stop` | POST | 优雅停止当前分析 |
| `/analysis/status` | GET | 当前分析状态（运行中 / 空闲 / 错误） |
| `/analysis/token_stats` | GET | 累计 token 与缓存命中率 |

## 队列

| 端点 | 方法 | 用途 |
|---|---|---|
| `/queue` | GET / PUT | 查 / 改队列项 |
| `/queue/scan` | POST | 扫描工作区入队 |
| `/queue/scan_workspace` | POST | 扫描 workspace 目录 |
| `/queue/remove` | POST | 从队列移除（移入回收站，不删数据） |
| `/queue/move_up` | POST | 上移一项 |
| `/queue/move_down` | POST | 下移一项 |
| `/queue/delete_book` | POST | 永久删除一本书 |
| `/queue/reset_item` | POST | 重置一项（清空分析结果） |
| `/queue/clear` | POST | 清空队列 |

## 书目

| 端点 | 方法 | 用途 |
|---|---|---|
| `/books` | GET | 列出所有已注册书目 |
| `/books/{id}/results` | GET | 某书的全部章节结果 |
| `/books/{id}/report` | GET | 最终总结报告 |
| `/books/{id}/ledger` | GET | 伏笔账本 |
| `/books/{id}/characters` | GET | 角色卡数据 |
| `/books/{id}/chapter/{n}` | GET | 单章结果 |

## 总结

| 端点 | 方法 | 用途 |
|---|---|---|
| `/summary/status` | GET | 当前总结状态 |
| `/summary/start` | POST | 启动 4 阶段最终总结 |
| `/summary/stop` | POST | 优雅停止总结 |

## 风格

| 端点 | 方法 | 用途 |
|---|---|---|
| `/style/start` | POST | 启动风格分析 |
| `/style/status` | GET | 风格分析状态 |
| `/style/result/{id}` | GET | 风格分析结果 |

## 可视化

| 端点 | 方法 | 用途 |
|---|---|---|
| `/viz/timeline/{id}` | GET | 时间线数据（事件 + 伏笔 + 50 类筛选） |
| `/viz/graph/{id}` | GET | 角色关系图 |
| `/viz/map/{id}` | GET | 地点地图 |

## 聚合

| 端点 | 方法 | 用途 |
|---|---|---|
| `/aggregate/run` | POST | 跑聚合（生成 11 类 JSON） |
| `/aggregate/{id}/files` | GET | 列出该书的聚合文件 |
| `/aggregate/{id}/file/{name}` | GET | 读单个聚合文件（带路径穿越防护） |
| `/aggregate/{id}/excel` | POST | 导出 Excel 多 Sheet |

## 切分

| 端点 | 方法 | 用途 |
|---|---|---|
| `/splitter/preview` | POST | 切分预览（不落盘） |
| `/splitter/save` | POST | 切分并落盘 |
| `/splitter/batch` | POST | 批量切分 |
| `/splitter/infer_name` | POST | 推断书名 |

## 设置

| 端点 | 方法 | 用途 |
|---|---|---|
| `/settings` | GET / PUT | 读 / 写配置（运行中 PUT 拒绝） |
| `/settings/presets` | GET | 厂商预设列表 |
| `/settings/models` | GET | 探测某 base_url 下的可用模型 |
| `/settings/preview/models` | POST | 真实调用模型列表接口（带 key） |

## 伏笔

| 端点 | 方法 | 用途 |
|---|---|---|
| `/foreshadow/categories` | GET | 50 类定义 + 保留集 + 阈值 |

## Prompt

| 端点 | 方法 | 用途 |
|---|---|---|
| `/prompt/preview` | POST | 真实 prompt 调试（用真实 LLM key 拼装 system + user） |

## 工作区

| 端点 | 方法 | 用途 |
|---|---|---|
| `/workspace/novels` | GET | 列出工作区所有书 |
| `/workspace/archives` | GET | 列出归档区所有书 |
| `/workspace/archive` | POST | 归档一本书 |
| `/workspace/archive_all` | POST | 归档所有书 |
| `/workspace/delete_archive` | POST | 删除归档 |

## WebSocket

| 端点 | 方向 | 用途 |
|---|---|---|
| `/ws/progress` | 服务端推送 | 实时日志 / 进度 / token 广播 |

### 消息类型

| 类型 | payload 字段 | 内容 |
|---|---|---|
| `log` | `level`, `message`, `ts` | 单行日志 |
| `progress` | `percent`, `current`, `total` | 整体进度 0-100 |
| `block_start` | `block_id`, `chapters` | 某块开始分析 |
| `block_done` | `block_id`, `status`, `result_path` | 某块分析完成 |
| `state_change` | `from`, `to`, `reason` | 队列 / 总结状态机切换 |
| `token_stats` | `total`, `cached`, `hit_rate` | token 累计与缓存命中率快照 |
| `summary_progress` | `stage` (1-4), `percent`, `message` | 总结 4 阶段细分进度 |
| `discovery` | `new_books`, `removed_books` | 工作区扫描发现 / 移除新书 |

所有消息共享 envelope：

```json
{
  "type": "log",
  "ts": "2026-09-12T08:30:00.123Z",
  "payload": { ... }
}
```

## 健康检查

| 端点 | 方法 | 用途 |
|---|---|---|
| `/health` | GET | uvicorn 就绪探针（桌面端轮询这个判断可开窗） |
| `/docs` | GET | Swagger UI |
| `/openapi.json` | GET | OpenAPI schema |
