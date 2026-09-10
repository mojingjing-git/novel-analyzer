# 切章器 baseline 报告

| 字段 | 值 |
|---|---|
| **日期** | 2026-09-02 |
| **阶段** | Phase 1（v2 plan baseline） |
| **测试集** | 7 本 Project Gutenberg 外文书 |
| **运行结果** | 2/7 章节数 **形式达标**（但含 silent-failure），真实可用率 0/7 |

> **重要纠偏**：原任务派工说"5-6 本应该 fail，1-2 本可能意外通过"；实测数字上看 2/7 fail，5/7 通过章节数阈值。**但深入核查发现 5/7"通过"的多是 silent failure**（详见 §3 🔴 Bug-1、🔴 Bug-2），真实可用率是 0/7。这一发现把 Phase 2 的修复范围扩大了。

---

## 1. 下载结果

| Gutenberg ID | 文件 | 大小 | 状态 | URL 模板 |
|---|---|---:|---|---|
| 1342 | Pride and Prejudice | 772,384 B | ✅ | cache/epub/1342/pg1342.txt |
| 84 | Frankenstein | 448,885 B | ✅ | cache/epub/84/pg84.txt |
| 345 | Dracula | 890,348 B | ✅ | cache/epub/345/pg345.txt |
| 98 | A Tale of Two Cities | 807,197 B | ✅ | cache/epub/98/pg98.txt |
| 2701 | Moby-Dick | 1,276,263 B | ✅ | cache/epub/2701/pg2701.txt |
| 64317 | The Great Gatsby | 306,553 B | ✅ | cache/epub/64317/pg64317.txt |
| 1661 | Sherlock Holmes | 607,606 B | ✅ | cache/epub/1661/pg1661.txt |
| **合计** | | **5.11 MB** | **7/7** | 第一套 URL 全部命中，无需 fallback |

- 脚本路径：`tests/fixtures/gutenberg/_download.ps1`
- 已现场修复 v2 引入的 2.1.D 新 bug：try 块开头加 `Remove-Item $dst -ErrorAction SilentlyContinue` 清掉部分写入（200 字节 HTML 错误页）

---

## 2. baseline 跑测结果

`pytest tests/test_splitter_english_baseline.py -v -s` 实际输出（详见 `tests/fixtures/baseline_run.utf8.log`）。

| ID | 书名 | 期望章节数 | 实际章节数 | 章节数形式达标 | metadata.title | metadata.author | 卷数 | pattern_name |
|---:|---|---:|---:|:---:|---|---|---:|---|
| 1342 | Pride and Prejudice | ≥50 | **21** | ❌ | 'The Project Gutenberg eBook of Pride and Prejudice' | '' | 0 | 兜底第X章 |
| 84 | Frankenstein | ≥20 | **48** | ✅ | 'The Project Gutenberg eBook of Frankenstein; or, The Modern Prometheus' | '' | 0 | 英文Chapter |
| 345 | Dracula | ≥0 | **25** | ✅（已知限制）| 'The Project Gutenberg eBook of Dracula' | '' | 0 | 兜底第X章 |
| 98 | A Tale of Two Cities | ≥30 | **21** | ❌ | 'The Project Gutenberg eBook of A Tale of Two Cities' | '' | 0（需 ≥2）| 兜底第X章 |
| 2701 | Moby-Dick | ≥100 | **270** | ✅（但 2x 重复）| 'The Project Gutenberg eBook of Moby Dick; Or, The Whale' | '' | 2（BOOK III, part i）| 英文Chapter |
| 64317 | The Great Gatsby | ≥5 | **21** | ✅（但章节是 license 段！）| 'The Project Gutenberg eBook of The Great Gatsby' | '' | 0 | 兜底第X章 |
| 1661 | Sherlock Holmes | ≥8 | **21** | ✅（但章节是 license 段！）| 'The Project Gutenberg eBook of The Adventures of Sherlock Holmes' | '' | 0 | 兜底第X章 |

**headline**：2/7 fail，5/7 pass min_chapters 阈值。**但深入核查后调整**：

- ✅ **真通过**：只有 **#84 Frankenstein**（48 章真实 Arabic-numeral chapter）。
- ⚠️ **形式通过但 silent-failure**：
  - **#2701 Moby-Dick**：135 章被算成 270（Table of Contents 与正文各匹配一次）。
  - **#64317 Gatsby** / **#1661 Sherlock** / **#1342 Pride** / **#98 Tale** / **#345 Dracula**：切出的"章节"实际是 Gutenberg license 条款（`1.A. By reading...` `1.B. ...`），不是小说内容。
- ❌ **fail**：#1342、#98 章节数不足。

---

## 3. 暴露的 Bug 清单

按严重度排序：

### 🔴 高（silent failure：用户看到"切分成功"但实际不可用）

#### Bug-1：**Gutenberg license header 被当作章节切出**
- **现象**：`split_text()` 没有任何"从 `*** START OF PROJECT GUTENBERG` 之后才算正文"的过滤，直接对全文做章节正则。
- **影响**：5/7 本 baseline 都触发。结果里前 20+ 章节全是 license 的 `1.A.` `1.B.` `1.C.` `1.D.` `1.E.1.` ... `1.F.6.` 条款。
- **证据**（#64317 Gatsby 切出的"章节"前 5 个）：
  ```
  [0] '1.A. By reading or using any part of this Project Gutenberg'
  [1] '1.B. Project Gutenberg is a registered trademark...'
  [2] '1.C. The Project Gutenberg Literary Archive Foundation...'
  [3] '1.D. The copyright laws of the place where you are located...'
  [4] '1.E. Unless you have removed all references to Project Gutenberg:'
  ```
- **触发正则**：`数字点编号` `r"^\d+[\.、]\s*\S"` 命中 license 的 `1.A.` `1.B.` 等。
- **修复方向**（Phase 2 顺手做或单独立 T2.5）：
  1. 找到 `*** START OF (THE|THIS) PROJECT GUTENBERG` 标记，截取之后的内容做切分；或
  2. 在 metadata 提取阶段识别"Project Gutenberg eBook"标题行后直接 skip 整段 license。
- **plan 范围**：当前 plan T2.1-T2.4 没有覆盖这个 bug，需要在 v3 plan 增补或 Phase 2 前置任务。

#### Bug-2：**Moby-Dick Table of Contents 与正文双重匹配**
- **现象**：135 章全部 `CHAPTER N. Title` 在 Contents 和正文中各出现 1 次 → 切出 270 个块。
- **影响**：`dedup_count` 没用——内容 MD5 是按章节内容算的，contents 里只写 "CHAPTER 1. Loomings."（一行），正文里是整章，所以 MD5 不同。
- **修复方向**：
  1. 识别并截断 Contents 段（典型标志：大量 "CHAPTER N. Title" 集中出现且无段落正文，跟 `*** START OF` 之间的区域）。
  2. 或对章节标题做基于正文的 dedup（两章标题相同 → 合并 / 删短的那个）。
- **plan 范围**：同上，需补。

#### Bug-3：**英文 Roman numeral chapter 不识别**
- **现象**：`CHAPTER I.` `CHAPTER II.` `CHAPTER III.` 这类古典英文小说标准格式完全切不出。
- **影响**：
  - #1342 Pride and Prejudice：实际 61 个 `^(Chapter|CHAPTER)`，只切出 21（fallback 命中 license 段）。
  - #98 A Tale of Two Cities：实际 45 个，只切出 21（fallback 命中 license 段）。
  - #345 Dracula：实际 54 个，只切出 25（fallback 命中 license 段）。
  - #1661 Sherlock：用的是 `I. A SCANDAL IN BOHEMIA` 格式（无 "Chapter" 前缀）—— 也不识别。
- **现有正则**：`英文Chapter = r"^chapter\s+\d+"` 只匹配 Arabic 数字。
- **修复**：plan T2.1 已规划加 2 条正则（Roman + 英文数词）—— **必须做**，否则 6/7 本都废。但 plan 没覆盖 **"I. A SCANDAL IN BOHEMIA"**（无 "Chapter" 前缀、只用 Roman + 标题）这种格式，需要额外加 1 条。

#### Bug-4：**英文作者完全识别不出**
- **现象**：7/7 本 `metadata.author` 都是空字符串 `''`。
- **现有正则**：`_extract_metadata` 只匹配中文 `作者/著`。
- **影响**：英文小说 author 永远为空。Baseline 已确认。
- **修复**：plan T2.3 已规划加 `(?:by|author)[:\s]+` 模式 —— **必须做**。

### 🟡 中

#### Bug-5：**英文卷归属"Book the First/Second/Third"不识别**
- **现象**：#98 A Tale of Two Cities 实际有 3 个 Book（`Book the First--Recalled to Life` / `Book the Second--the Golden Thread` / `Book the Third--the Track of a Storm`），splitter 0 卷。
- **影响**：Tale of Two Cities 整本书的"卷"信息丢失，章节无法按卷分组。
- **现有正则**：`英文卷 = r"^(volume|book|part)\s+[\dIVXLC]+"` 只匹配 `Book III` 这种，不匹配 `Book the First`。
- **修复**：plan T2.4 已规划加 `Book the (First|Second|...)` 和 `Book [IVX]` —— **必须做**。

#### Bug-6：**Moby-Dick 错误识别"BOOK III"和"part i"为卷**
- **现象**：Moby-Dick 实际是 **135 章单卷结构**，但 `总卷数=2`，卷集合是 `{'BOOK III', 'part i'}`（注：实际是 `BOOK III.` 命中 + `part i.` 误命中）。
- **影响**：把"BOOK III."当成一个卷起点，导致章 67（实际是 CHAPTER 67）之前的章节被归到一个"前卷"，之后被分到"BOOK III 卷"——卷归属严重错乱。
- **修复**：plan T2.4 也要给 Book 关键字收紧锚点，避免与正文中"BOOK III. ... The Ship"这种章节行误撞。

#### Bug-7：**metadata.title 命中 "The Project Gutenberg eBook of X" 而不是 "X"**
- **现象**：所有 7 本 title 都带 "The Project Gutenberg eBook of " 前缀。
- **影响**：title 字段不可直接用，得后处理剥前缀。
- **现有逻辑**：`_extract_metadata` 取前 8 行第一个无章节关键字的短行——`Title: Pride and Prejudice` 这行被 `Title:` 关键字过滤掉了（"Title" 既不是章也不是回也不是卷，但 splitter 把它当 metadata 行过滤掉——不对，应该是 metadata 提取逻辑直接取这行）。
- **修复方向**：
  1. 显式识别 `Title: X` / `Author: X` 这种 PG header 格式，直接取值。
  2. 或在 splitter 入口跳过 Gutenberg header 到 `*** START OF` 之后（与 Bug-1 一起治）。

### 🟢 低

#### Bug-8：**Sherlock Holmes 章节切分方式不对**
- **现象**：用的是 `I. A SCANDAL IN BOHEMIA` / `II. THE RED-HEADED LEAGUE` 这种"无 Chapter 前缀 + Roman + 标题"格式。
- **现有正则**完全无法识别（`数字点编号` 命中 license 而不是小说，因为 license 也在前 800 行扫描区间内）。
- **修复**：加 1 条 `^[IVX]+\.\s+[A-Z][A-Z\s,]+$`（Roman + 标题）—— 不在 v2 plan 里，但 v3 建议加。

#### Bug-9：**#84 Frankenstein pattern_name "英文Chapter" 实际是 48 章** —— **看似 OK** 但需要 T2.x 之后回归验证不会回归。
- **现象**：Frankenstein 用的是 `Chapter 1` 格式（Arabic 数字）所以匹配成功。
- **风险**：现有 `^chapter\s+\d+` 正则对 `Chapter 1` 没问题，但如果某本英文书用 `Chapter 1.` `Chapter 1:`，需确认 dot/colon 是否被允许（计划 v2 review 已确认 line 241 有 `re.IGNORECASE`）。

---

## 4. 已知限制确认

| 限制 | 来源 | 实测确认 |
|---|---|---|
| Dracula 等日记体无显式 Chapter 标记 | plan §0 / §3 | ❌ **未确认** — Dracula 实际有 `CHAPTER I.` ~ `CHAPTER XXVII.` 27 章 Roman 标记，应能切；baseline 只切 25 是因 Roman numeral 正则缺失 + license 段污染 |
| 日文片假名标注章节（如「プロローグ」）不保证识别 | plan §0 已知限制 | 未测试（不在 baseline 范围）|
| metadata.author 在外文书场景下识别 | — | ❌ 7/7 全空 |

> **重要调整**：plan §3 验收矩阵里 "Dracula 不报错 + `metadata.author` 含 'Stoker'" —— 实测 author 7/7 都空，连 Dracula 也不例外。**Phase 2 必须 T2.3 修复才能达到 plan §3 验收**。

---

## 5. Phase 2 / Phase 3 / Phase 4 计划影响

按本次 baseline 暴露的真实问题，**v2 plan 的 Phase 2 必须做下列 4 件事**才能让 7 本 Gutenberg 全部切出合理结果：

1. **T2.1** ✅ 计划内：加 Roman numeral + 英文数词章节正则（修 Bug-3 主线）
2. **T2.2** ✅ 计划内：`_extract_chapter_num` 支持 Roman + 英文数词（修 Bug-3 副线）
3. **T2.3** ✅ 计划内：英文 `by/author` 模式识别 author（修 Bug-4）
4. **T2.4** ✅ 计划内：`Book the First/Second/Third` + `Book [IVX]` 卷归属（修 Bug-5、Bug-6）
5. **🆕 T2.5**（**建议在 v3 加**）：Gutenberg header 截断 + Contents 段截断（修 Bug-1、Bug-2）—— **没有这个，Bug-1 会让 Bug-3 修完之后仍切出 license 段当章节**
6. **🆕 T2.6**（**建议在 v3 加**）：英文 `I. A SCANDAL IN BOHEMIA` 无前缀 Roman + 标题 模式（修 Bug-8）
7. **🆕 T2.7**（**建议在 v3 加**）：`_extract_metadata` 识别 `Title: X` / `Author: X` 显式 header（修 Bug-7）

> 如果 v3 plan 不增 T2.5/2.6/2.7，**Phase 2 完成后仍有 Bug-1/Bug-2 残留** —— Gatsby / Sherlock / Tale of Two Cities / Pride and Prejudice / Dracula 的"切分结果"前 20+ 章节仍是 license 条款，必须手工过滤。

---

## 6. 验收签字

| 项 | 标准 | 实际 | 通过 | 日期 | 签字 |
|---|---|---|:---:|---|---|
| 7 本下载 | 7/7 成功 | **7/7** | ✅ | 2026-09-02 | worker |
| 7 本跑测 | 7/7 形式达标 | **2/7 真达标 / 5/7 silent-fail** | ❌ | 2026-09-02 | worker |
| Baseline 报告 | 本文 | 已写 | ✅ | 2026-09-02 | worker |
| Phase 2 任务增补 | 复审 v2 plan 决定是否加 T2.5/2.6/2.7 | 见 §5 | — | — | 待用户 |
