# 切章器国际化 + 边界加固 · 实施 Plan

| 字段 | 值 |
|---|---|
| **日期** | 2026-09-02 |
| **目标** | 把 `backend/services/splitter_service.py` 改成"包罗万象"切章器 |
| **验收** | 完整可用：所有语种 + 所有边界 case 都能正确切分，回归无破坏 |
| **范围** | A/B/C 三个 workstream，**不含**商业化路径（D 任务延期）|
| **用户偏好** | Q1=包罗万象 / Q2=听我的（用项目 .venv）/ Q3=最终验收完整可用 / Q4=商业化晚点 |

---

## 0. 验收标准（Definition of Done）

✅ **必须全部满足** 才算"完整可用"：

- [ ] **A 验收**：7 本 Project Gutenberg 外文书 + 5 本中文网文，跑现有切章器，**全部 12 本得到合理切分结果**（不要求完美章节数，但章节数 > 1，metadata 至少有 title）
  - **已知限制**：Dracula 等日记体无显式 Chapter 标记的英文小说，**不保证章节数 ≥ 2**，只看 metadata 是否能识别（作者/标题）
- [ ] **B 验收**：4 类边界 case（空文件 / 单章节 / 5 行诗集 / 20MB 单章）**全部不报错**，返回合理结果
- [ ] **C 验收**：所有现有 pytest 用例**无回归**（`python -m pytest backend/tests/` 全过，**实际用例数以 `pytest --collect-only -q | Measure-Object -Line` 输出为准**）<!-- v2 review fix: B2 Dracula 已知限制 + B1 用例数说明 + Y5 50→20MB -->
- [ ] **D 验收**：新增 1 份外文回归 fixture + 1 份边界 fixture（`tests/fixtures/gutenberg/` + `tests/test_splitter_edge_cases.py`）
- [ ] **E 验收**：`splitter_service.py` docstring 末尾追加**已知限制**清单（如 Dracula 等隐式章节不保证）

**已知限制**（不在本次修复范围）：
- 未测试日文原文；片假名标注的章节（如「プロローグ」）**不保证识别**
- Dracula 等日记体英文小说没有显式 Chapter 标记 → 不保证章节数 ≥ 2

任何一项没满足 = 没完成，**继续修**直到全过。<!-- v2 review fix: Y2 加日文已知限制 + B2 强化 Dracula 说明 -->

---

## 1. 任务清单（Checklist）

### Phase 1：跑 Baseline（**Day 1 晚上**）

- [ ] **T1.1** — 创建 `tests/fixtures/gutenberg/` 目录
- [ ] **T1.2** — 下载 7 本外文书（5MB，PowerShell 见 §6.1）
- [ ] **T1.3** — 写 `tests/test_splitter_english_baseline.py`（baseline 跑测脚本，**只跑不修**）
- [ ] **T1.4** — 跑 baseline，记录每本切出几章、metadata 是什么
- [ ] **T1.5** — 写 `docs/REPORT-splitter-baseline-2026-09-02.md`（错误清单 + 优先级）
- [ ] **T1.6** — 用户过目 baseline 报告（异步，**24 小时未确认则按 plan §1 任务列表默认执行**，不阻塞 Phase 2）<!-- v2 review fix: Y1 加 24h 兜底，不阻塞 Phase 2 -->

### Phase 2：核心修复（**Day 2-3**）

- [ ] **T2.1** — 加 2 条英文章节正则到 `CHAPTER_PATTERNS`（行 99-112，**book 关键字由 T2.4 在 VOLUME_PATTERNS 处处理，不进 CHAPTER_PATTERNS**）<!-- v2 review fix: B3 删 book 那条，改 3 条为 2 条 -->
- [ ] **T2.2** — 改 `_extract_chapter_num`（行 211-220）支持罗马数字
- [ ] **T2.3** — 加英文作者正则到 `_extract_metadata`（行 361-364）
- [ ] **T2.4** — 改 `VOLUME_PATTERNS`（splitter_service.py:146 附近）追加 `book` 关键字支持，支持 `Book the X` 和 `book [IVX]`
- [ ] **T2.6** — 每改一处跑相关 fixture，确认无回归
- [ ] **T2.7** — 重新跑 7 本外文书，对比 baseline，确认改善

### Phase 3：边界加固（**Day 4-5**）

- [ ] **T3.1** — 写 `tests/test_splitter_edge_cases.py`（4 个边界 case）
- [ ] **T3.2** — 实现空文件防御（早返回 + 友好错误）
- [ ] **T3.3** — 实现单章节 fallback（已经有，看是否需要强化）
- [ ] **T3.4** — 实现 5 行诗集处理（<= 10 行 / 1000 字算"超短文"）
- [ ] **T3.5** — 实现超长单章拆分（已有 max_words，验证拆分逻辑；上限 20MB 单章）<!-- v2 review fix: Y5 50MB→20MB -->
- [ ] **T3.6** — 跑所有边界 case，全部通过

### Phase 4：全面测试 + 验收（**Day 6**）

- [ ] **T4.1** — 跑全部 7 本外文书 + 5 本中文网文（混合测试集）
- [ ] **T4.2** — 跑全部 4 个边界 case
- [ ] **T4.3** — 跑 `pytest backend/tests/` 完整回归（354 用例，实际数 = `pytest --collect-only -q | Measure-Object -Line`）<!-- v2 review fix: B1 420→354 + 加 pytest 计数命令 -->
- [ ] **T4.4** — 修任何回归 / 漏掉的 case
- [ ] **T4.5** — 更新 `CHANGELOG.md` 加 P1 修复条目
- [ ] **T4.6** — 更新 `splitter_service.py` docstring 末尾加"已知限制"清单

### Phase 5：交付（**Day 7**）

- [ ] **T5.1** — 写交付报告 `docs/DELIVER-splitter-i18n-2026-09.md`
- [ ] **T5.2** — 用户最终验收
- [ ] **T5.3** — git commit（如果项目有 git 远端）

---

## 2. 详细改动点（代码级别）

### T2.1 加英文章节正则（`splitter_service.py:99-112`）

在 `CHAPTER_PATTERNS` 列表末尾追加 **2 条**（book 关键字由 T2.4 在 `VOLUME_PATTERNS` 处处理，**不进** `CHAPTER_PATTERNS`）：

```python
# 英文罗马数字章节（Jane Austen、Dickens 等古典英文小说）
r"^[Cc]hapter\s+[IVX]+[\.\s]?",
# 英文数词章节（one/two/.../nineteen）
r"^[Cc]hapter\s+(?:one|two|three|four|five|six|seven|eight|nine|ten"
r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen"
r"|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy"
r"|eighty|ninety|hundred)\b[\s\.:]?",
```

> book 关键字的英文卷归属由 T2.4 在 `VOLUME_PATTERNS` 处处理，不进 `CHAPTER_PATTERNS`。<!-- v2 review fix: B3 删 book 那条 -->

**注意**：`splitter_service.py:241` 现有正则已有 `re.IGNORECASE` flag，**大写 `Chapter 1` 不会漏**——本次 plan 不需要修复大写 Chapter。<!-- v2 review fix: v2 review 加 IGNORECASE 已存在说明，避免误改 -->

同步把现有的 `r"^chapter\s+\d+"` 改为 `r"^[Cc]hapter\s+\d+"`（统一大小写处理）：

```python
r"^[Cc]hapter\s+\d+",                          # 现代英文
r"^[Cc]hapter\s+[IVX]+[\.\s]?",                # 古典英文罗马
r"^[Cc]hapter\s+(?:one|two|...)\b[\s\.:]?",    # 英文数词
# book 关键字在 VOLUME_PATTERNS（T2.4）处理
```

**验证**：`tests/test_splitter_mixed_formats.py` 不挂

### T2.2 罗马数字转阿拉伯（`splitter_service.py:211-220`）

`_extract_chapter_num` 当前只匹配 `\d+`。加一段：

```python
def _extract_chapter_num(text: str) -> Optional[int]:
    # 现有阿拉伯数字逻辑
    m = re.search(r"chapter\s+(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # 罗马数字（V/X/I/L/C/D/M）
    roman = re.search(r"chapter\s+([IVXLCDM]+)[\.\s]?", text, re.IGNORECASE)
    if roman:
        return _roman_to_int(roman.group(1).upper())
    # 英文数词
    word_match = re.search(r"chapter\s+([\w\- ]+?)\b", text, re.IGNORECASE)  # 含连字符和空格，支持 "twenty-one" / "twenty one" / "twenty"
    if word_match:
        num = _word_to_int(word_match.group(1).strip().lower())
        if num is not None:
            return num
    return None


_ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

def _roman_to_int(s: str) -> Optional[int]:  # 输入可能是 'twenty-one' / 'twenty one' / 'twenty' 多种形式，先过滤
    # 避免单词 "I"（代词）误判为罗马数字 I
    if len(s) < 2 and s != "I":  # 单字符且非 "I" → 肯定不是罗马数字
        return None
    total = 0
    prev = 0
    for c in reversed(s):
        cur = _ROMAN_MAP.get(c, 0)
        if cur < prev:
            total -= cur
        else:
            total += cur
        prev = cur
    return total


_WORD_TO_INT = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100,
}

def _word_to_int(word: str) -> Optional[int]:  # 输入可能是 'twenty-one' / 'twenty one' / 'twenty' 多种形式
    if word in _WORD_TO_INT:
        return _WORD_TO_INT[word]
    # 复合: "twenty-one" → 21 / "twenty one" → 21
    for sep in ("-", " "):
        if sep in word:
            parts = word.split(sep)
            total = 0
            for p in parts:
                v = _WORD_TO_INT.get(p)
                if v is None:
                    return None
                total += v
            return total
    return None
```<!-- v2 review fix: Y3 复合英文数词 `\w+` → `[\w\- ]+?` + `_roman_to_int` 加 I 兜底 + 注释说明多种输入形式 -->

**验证**：在 `tests/test_splitter_mixed_formats.py` 加 3 个 case（罗马/英文数词/复合）

### T2.3 英文作者识别（`splitter_service.py:361-364`）

```python
# 现有中文版
m = re.search(r"(?:作者|著)\s*[:：]?\s*(.+)", text)
# 新增英文版（只在前 30 行内匹配，避免命中正文里的"by someone"）
# 字符类含逗号、撇号（如 O'Brien, Jr.），限 60 字符
# 加锚点 (?:^|\n) 防止跨行匹配
m_en = re.search(
    r"(?:^|\n)(?:by|author)[:\s]+([A-Za-z\.\- ,']{1,60}?)(?:\n|$)",
    "\n".join(text.splitlines()[:30]),  # 前 30 行
    re.IGNORECASE
)
```

**注意**：非贪婪 + 字符类限定 60 字符 + 锚点 `(?:^|\n)` 防止跨行匹配 + 前 30 行限制避免误命中正文。<!-- v2 review fix: Y4 字符类 + 锚点 + 限 30 行 + 限 60 字符 -->

### T2.4 卷归属支持 Book the X

**位置**：`splitter_service.py:146` 附近的 `VOLUME_PATTERNS` 列表（**不是** `CHAPTER_PATTERNS`）。

`_is_volume_start` 当前是硬编码关键字过滤。在 `VOLUME_PATTERNS` 列表末尾追加：

```python
# 英文卷归属（Book the First、Book I）
r"^[Bb]ook\s+(?:the\s+)?(?:first|second|third|fourth|fifth|sixth"
r"|seventh|eighth|ninth|tenth|[IVX]+)[\.\s]?$",
```

> book 关键字的英文卷归属放在 `VOLUME_PATTERNS`（T2.4），**不**进 `CHAPTER_PATTERNS`（T2.1）。<!-- v2 review fix: B3 明确 VOLUME_PATTERNS 位置 + 代码片段 -->

---

## 3. 测试方案

### 测试集组成

| 测试文件 | 用途 | 用例数 |
|---|---|---|
| `tests/test_splitter_english_baseline.py` | 跑 7 本外文书，记录实际切分结果 | 7 |
| `tests/test_splitter_edge_cases.py` | 4 个边界 case | 4 |
| `tests/test_splitter_mixed_formats.py` | **加** 3 个罗马/英文数词/复合 case | +3 |
| `tests/test_splitter_*.py`（现有）| 跑回归 | 现有 5 个文件全跑 |

### 验收测试矩阵

| 场景 | 测试文件 | 通过标志 |
|---|---|---|
| Pride and Prejudice #1342 | test_splitter_english_baseline.py | `len(chapters) >= 60` 且 `metadata.author` 含 "Austen" |
| Frankenstein #84 | test_splitter_english_baseline.py | `len(chapters) >= 20` |
| Dracula #345 | test_splitter_english_baseline.py | 不报错 + `metadata.author` 含 "Stoker" |
| A Tale of Two Cities #98 | test_splitter_english_baseline.py | `len(volumes) >= 2`（Book the First 识别）|
| Moby-Dick #2701 | test_splitter_english_baseline.py | `len(chapters) >= 100` |
| 空文件 | test_splitter_edge_cases.py | 抛 `ValueError` 或返回空 `chapters.json` |
| 单章节 | test_splitter_edge_cases.py | 返回 1 章不报错 |
| 5 行诗集 | test_splitter_edge_cases.py | 返回 1 章不报错 |
| 20MB 单章 | test_splitter_edge_cases.py | 自动拆为 2+ 章（pytest timeout=180s）<!-- v2 review fix: Y5 50→20MB + timeout 180s --> |
| 中文回归 | test_splitter_*.py（所有）| 全过 |
| 现有 pytest | `pytest backend/tests/` | 354 用例全过（实际数 = `pytest --collect-only -q | Measure-Object -Line`）<!-- v2 review fix: B1 420→354 --> |

---

## 4. 风险与回滚

| 风险 | 触发 | 缓解 / 回滚 |
|---|---|---|
| 英文正则误命中中文 | 改完跑中文测试挂 | 收紧正则 + 加 `\b` 边界 |
| 罗马数字识别有歧义 | 真实小说 "Chapter IIII" 极少但有 | 暂不处理极少见 case |
| Project Gutenberg 下载慢 | 网络问题 | 用 `--max-time 30` 限制单本下载 |
| 20MB 单章测试超时 | pytest timeout | `pytest --timeout=180` |<!-- v2 review fix: Y5 50→20MB + timeout 60→180 -->
| 改动破坏中文切分 | 中文回归挂 | `git stash` 回滚 + 分批改 |
| 测试发现 N 个新 bug | Dracula 切 1 章、其他有奇怪行为 | 列 backlog，**不**在本次范围 |

**回滚命令**：

```powershell
cd F:\AI\01_项目\小说分析器
git checkout backend/services/splitter_service.py
```

---

## 5. 时间线（5-7 天）

```
[Day 1 - 今晚 2 小时]   Phase 1 baseline
[Day 2 - 3 小时]         Phase 2 核心修复 (T2.1-T2.4)
[Day 3 - 2 小时]         Phase 2 收尾 (T2.6-T2.7)<!-- v2 review fix: Y2 删 T2.5 后任务编号顺移 -->
[Day 4 - 3 小时]         Phase 3 边界 case (T3.1-T3.6)
[Day 5 - 2 小时]         Phase 4 全面测试 (T4.1-T4.6)
[Day 6 - 1 小时]         Phase 5 交付 (T5.1-T5.3)
[Day 7]                  缓冲 / 用户最终验收
```

**总投入**：**约 13-15 小时**（一周内可完成）

---

## 6. 命令清单（用户可复制粘贴）

### 6.1 下载 7 本外文书（PowerShell）<!-- v2 review fix: B2 加 3 个 URL 模板 fallback + 网络参数 + 断点续传 -->

```powershell
cd F:\AI\01_项目\小说分析器
mkdir tests\fixtures\gutenberg -Force
$ids = 1342, 84, 345, 98, 2701, 64317, 1661
$urlTemplates = @(
    "https://www.gutenberg.org/cache/epub/{0}/pg{0}.txt",
    "https://www.gutenberg.org/files/{0}/{0}-0.txt",
    "https://www.gutenberg.org/ebooks/{0}.txt.utf-8"
)
foreach ($id in $ids) {
    $dst = "tests\fixtures\gutenberg\gutenberg_$id.txt"
    if (Test-Path $dst) { Write-Host "已存在: $id"; continue }
    $downloaded = $false
    foreach ($template in $urlTemplates) {
        $url = $template -f $id
        Write-Host "尝试: $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $dst -TimeoutSec 60 -UseBasicParsing
            if ((Get-Item $dst).Length -gt 1024) { $downloaded = $true; break }
        } catch { Write-Warning "失败: $url" }
    }
    if (-not $downloaded) { Write-Warning "全部 fallback 失败: $id" }
}
Get-ChildItem tests\fixtures\gutenberg\ | ForEach-Object { "{0} : {1:N0} bytes" -f $_.Name, $_.Length }
```

### 6.2 跑 Baseline（先看现状）

```powershell
.venv\Scripts\Activate.ps1
cd F:\AI\01_项目\小说分析器
pytest backend\tests\test_splitter_mixed_formats.py -v
```

### 6.3 跑回归（每天结束前必跑）

```powershell
pytest backend\tests\ -q
```

### 6.4 跑中文网文测试（5 本用《轮回乐园》切好的章节）

```powershell
pytest backend\tests\test_splitter_silent_failure.py -v
pytest backend\tests\test_splitter_atomic.py -v
```

### 6.5 跑外文书（改完后）

```powershell
pytest backend\tests\test_splitter_english_baseline.py -v
```

### 6.6 跑边界 case

```powershell
pytest backend\tests\test_splitter_edge_cases.py -v
```

---

## 7. 交付物清单

| 文件 | 用途 | 何时交付 |
|---|---|---|
| `backend/services/splitter_service.py` | 改完的核心模块 | Day 3 |
| `tests/test_splitter_english_baseline.py` | 外文回归 fixture | Day 1 |
| `tests/test_splitter_edge_cases.py` | 边界 case | Day 4 |
| `tests/fixtures/gutenberg/*.txt` | 7 本外文测试数据 | Day 1 |
| `docs/REPORT-splitter-baseline-2026-09-02.md` | 修复前的错误清单 | Day 1 |
| `docs/DELIVER-splitter-i18n-2026-09.md` | 最终交付报告 | Day 6 |
| `CHANGELOG.md` | 加 P1 修复条目 | Day 5 |

---

## 8. 验收签字<!-- v2 review fix: Y6 加填法说明 + 日期 + 签字 + 文档数 3→5 -->

**填法说明**：
- "标准"列是 plan 预定的目标值
- "实际"列由执行 agent 填写 N/M + 一句结论（如 "12/12（详见 DELIVER §3）"）
- "日期"列填 YYYY-MM-DD
- "签字"列填执行人（如 "worker"）+ 用户最终确认（如 "墨璟璟"）
- 任何一行 ✗ = plan 未完成，必须继续修

| 项 | 标准 | 实际 | 通过 | 日期 | 签字 |
|---|---|---|---|---|---|
| 外文 7 本全跑通 | 12/12 切分合理 | ___ | ☐ | ____ | ____ |
| 边界 4 case 全过 | 4/4 不报错 | ___ | ☐ | ____ | ____ |
| 中文回归无破坏 | 354 pytest 全过（实际数 = `pytest --collect-only -q | Measure-Object -Line`）<!-- v2 review fix: B1 420→354 --> | ___ | ☐ | ____ | ____ |
| 文档齐全 | 5 份文档（REPORT / DELIVER / 已知限制 / CHANGELOG / fixtures 清单）<!-- v2 review fix: Y6 3→5 份文档 --> | ___ | ☐ | ____ | ____ |

---

## 9. 不做的事（明确边界）

- ❌ 商业化路径选择（D 任务延后）
- ❌ 改切章器之外的代码（pipeline / llm_client / frontend 都不动）
- ❌ 跑 LLM 分析（《轮回乐园》81 章数据不动）
- ❌ 接单 / 写公众号 / 录教程
- ❌ 重写前端
- ❌ 重新打包发布
- ❌ 大规模重构（只做最小必要改动）
- ❌ 日文支持（不在本次范围）<!-- v2 review fix: Y2 加日文不在范围 -->

---

**Plan 状态**：✅ 已写好，等用户审核
**下一步**：用户回"开始"我立即执行 Phase 1 T1.1-T1.5
