# Windows 11 风格前端完整重做实施计划

> 目标：将当前 iOS Liquid Glass 视觉风格整体切换为 Windows 11 Fluent Design 风格。
> 执行方式：本计划面向可自动执行的 agent，按文件、按步骤、按验收标准推进。
> 参考规范：用户提供的《Windows 11 风格 UI 设计指南与设计规范》。

---

## 0. 执行前必读

### 0.1 范围

- 只重做 **前端视觉层**，不改变业务逻辑、API、数据结构、路由。
- 覆盖 `frontend/` 下全部 Vue/TS/CSS。
- 后端仅在“需要新增主题持久化接口”时才允许触碰；默认不修改后端。

### 0.2 非目标

- 不重构业务组件逻辑。
- 不改变页面功能与交互流程。
- 不引入新的 UI 框架/组件库。
- 不重写后端 API。

### 0.3 当前前端基线（已核实）

| 项目 | 数量 |
|---|---|
| Vue 文件 | 22 个 |
| TS 文件 | 8 个 |
| Vue + TS 总行数 | 约 5,500 行 |
| 全局样式 `frontend/src/main.css` | 822 行 |
| scoped 样式块 | 17 个 |
| 内联 `style` | 246 处 |
| 全局设计类 | `.glass-*` 约 30 个 |
| CSS 变量 | 约 30 个 |
| 图标 | `Icon.vue` 自绘 SVG，约 30 个 |

### 0.4 核心约束

1. **WebView2 兼容**：禁止使用 `backdrop-filter` 作为主要视觉手段；动画优先使用真实 DOM 元素 + `transform`/`opacity`。
2. **主题化**：所有颜色、圆角、阴影、字体、间距必须收敛到 CSS 变量，禁止在 Vue 模板中硬编码新颜色。
3. **Win11 克制原则**：强调色仅用于主按钮、选中态、焦点、链接、进度等关键交互，占比不超过 5%。
4. **不破坏功能**：每个文件改完必须保证模板结构、事件绑定、响应式数据不变。

---

## 1. 设计 Token 规范（新）

在 `frontend/src/main.css` 顶部重建 Win11 设计变量。

### 1.1 浅色主题 `:root`

```css
:root {
  color-scheme: light;

  /* 背景 / 图层 */
  --win-bg-base: #F3F3F3;            /* 窗口基底，模拟 Mica */
  --win-layer: #FFFFFF;              /* 内容层/卡片 */
  --win-control-alt: #F9F9F9;        /* 控件默认背景 */
  --win-control-hover: #F0F0F0;      /* 控件悬停 */
  --win-control-pressed: #E9E9E9;    /* 控件按下 */
  --win-stroke: #E5E5E5;             /* 分割线/边框 */
  --win-stroke-strong: #D0D0D0;      /* 更强边框 */

  /* 强调色 */
  --win-accent: #0067C0;
  --win-accent-hover: #005A9E;
  --win-accent-pressed: #004E8C;
  --win-accent-soft: rgba(0, 103, 192, 0.08);

  /* 文本 */
  --win-text-primary: rgba(0, 0, 0, 1);
  --win-text-secondary: rgba(0, 0, 0, 0.6);
  --win-text-disabled: rgba(0, 0, 0, 0.36);

  /* 功能色 */
  --win-success: #0F7B0F;
  --win-success-bg: rgba(15, 123, 15, 0.08);
  --win-danger: #C42B1C;
  --win-danger-bg: rgba(196, 43, 28, 0.08);
  --win-warning: #9D5D00;
  --win-warning-bg: rgba(157, 93, 0, 0.08);
  --win-info: #0067C0;
  --win-info-bg: rgba(0, 103, 192, 0.08);

  /* 圆角 */
  --win-radius-control: 4px;
  --win-radius-container: 8px;
  --win-radius-overlay: 8px;
  --win-radius-pill: 999px;

  /* 阴影 */
  --win-shadow-layer: 0 0 0 1px rgba(0, 0, 0, 0.04);
  --win-shadow-control: 0 1px 2px rgba(0, 0, 0, 0.06);
  --win-shadow-card: 0 8px 24px rgba(0, 0, 0, 0.10);
  --win-shadow-tooltip: 0 16px 32px rgba(0, 0, 0, 0.14);
  --win-shadow-flyout: 0 32px 64px rgba(0, 0, 0, 0.18);
  --win-shadow-dialog: 0 48px 96px rgba(0, 0, 0, 0.22);

  /* 字体 */
  --win-font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei", sans-serif;
  --win-font-caption: 12px;
  --win-font-body: 14px;
  --win-font-subtitle: 18px;
  --win-font-title: 20px;
  --win-font-display: 28px;

  /* 动效 */
  --win-duration-fast: 150ms;
  --win-duration-normal: 200ms;
  --win-duration-slow: 300ms;
  --win-ease: cubic-bezier(0.2, 0, 0, 1);
}
```

### 1.2 深色主题 `html.dark`

```css
html.dark {
  color-scheme: dark;

  --win-bg-base: #202020;
  --win-layer: #323232;
  --win-control-alt: #2B2B2B;
  --win-control-hover: #3A3A3A;
  --win-control-pressed: #353535;
  --win-stroke: #3D3D3D;
  --win-stroke-strong: #555555;

  --win-accent: #60CDFF;
  --win-accent-hover: #4CC2FF;
  --win-accent-pressed: #38B8FF;
  --win-accent-soft: rgba(96, 205, 255, 0.10);

  --win-text-primary: rgba(255, 255, 255, 1);
  --win-text-secondary: rgba(255, 255, 255, 0.7);
  --win-text-disabled: rgba(255, 255, 255, 0.4);

  --win-success: #6CCB5F;
  --win-success-bg: rgba(108, 203, 95, 0.12);
  --win-danger: #FF99A4;
  --win-danger-bg: rgba(255, 153, 164, 0.12);
  --win-warning: #FCE100;
  --win-warning-bg: rgba(252, 225, 0, 0.12);
  --win-info: #60CDFF;
  --win-info-bg: rgba(96, 205, 255, 0.12);

  --win-shadow-layer: 0 0 0 1px rgba(255, 255, 255, 0.04);
  --win-shadow-control: 0 1px 2px rgba(0, 0, 0, 0.3);
  --win-shadow-card: 0 8px 24px rgba(0, 0, 0, 0.3);
  --win-shadow-tooltip: 0 16px 32px rgba(0, 0, 0, 0.4);
  --win-shadow-flyout: 0 32px 64px rgba(0, 0, 0, 0.5);
  --win-shadow-dialog: 0 48px 96px rgba(0, 0, 0, 0.6);
}
```

### 1.3 兼容映射

为了减少全文件替换风险，在 `main.css` 中建立旧变量到新变量的兼容映射：

```css
:root {
  --bg-base: var(--win-bg-base);
  --bg-aurora: transparent;
  --bg-page: var(--win-bg-base);
  --glass-clear: var(--win-layer);
  --glass-frost: var(--win-layer);
  --glass-fill-subtle: var(--win-control-alt);
  --glass-fill-strong: var(--win-layer);
  --glass-rim-color: var(--win-stroke);
  --glass-border-subtle: var(--win-stroke);
  --glass-border-strong: var(--win-stroke-strong);
  --glass-shadow: var(--win-shadow-card);
  --glass-shadow-hover: var(--win-shadow-card);
  --glass-inner: none;
  --text-primary: var(--win-text-primary);
  --text-secondary: var(--win-text-secondary);
  --text-tertiary: var(--win-text-disabled);
  --divider: var(--win-stroke);
  --color-system-blue: var(--win-accent);
  --color-system-green: var(--win-success);
  --color-system-red: var(--win-danger);
  --color-system-orange: var(--win-warning);
  --color-system-gray: var(--win-text-secondary);
  --radius-glass-md: var(--win-radius-control);
  --radius-glass-lg: var(--win-radius-container);
  --radius-glass-xl: var(--win-radius-container);
  --radius-ios-md: var(--win-radius-control);
  --ease-fluid: var(--win-ease);
  --ease-spring: var(--win-ease);
}
```

> 该映射是“过渡层”，后续逐步把模板中的旧变量/旧类替换为新变量/新类，最终可删除映射。

---

## 2. 全局样式重写：`frontend/src/main.css`

### 2.1 必须删除/替换的内容

- 删除极光背景 `.app-aurora` 及 `@keyframes aurora-drift`。
- 删除 `body::before` 相关极光样式（如果还有残留）。
- 删除 `.sweep`、`sweep-move`、按钮扫光相关样式。
- 删除所有 `backdrop-filter` / `-webkit-backdrop-filter`。
- 删除玻璃内发光 `--glass-inner` 的复杂多层阴影，改为 Win11 低透明度阴影。
- 删除 iOS 大圆角体系，统一为 4px 控件 / 8px 容器。

### 2.2 必须新增/调整的内容

- `body` 背景使用 `var(--win-bg-base)`，可加一层极淡的径向渐变模拟 Mica 壁纸色调：
  ```css
  body {
    background: var(--win-bg-base);
    font-family: var(--win-font-family);
    font-size: var(--win-font-body);
    color: var(--win-text-primary);
  }
  ```
- 全局焦点样式：
  ```css
  :focus-visible {
    outline: 2px solid var(--win-accent);
    outline-offset: 1px;
  }
  ```
- 全局滚动条样式改为 Win11 风格（细、圆角、低饱和）。
- `html.dark` 同步所有变量。

### 2.3 `.glass-*` 基类重写对照

| 旧类 | 新语义 | 关键变化 |
|---|---|---|
| `.glass-card` | Win11 Card | 背景 `--win-layer`，圆角 `--win-radius-container`，1px `--win-stroke`，阴影 `--win-shadow-card` |
| `.glass-panel` | Win11 Layer/Panel | 同上，但阴影更弱 |
| `.glass-subtle` | Win11 Control Alt | 背景 `--win-control-alt` |
| `.glass-clear` | 透明层 | 背景 `rgba(255,255,255,0.6)` / 深色 `rgba(50,50,50,0.6)`，无模糊 |
| `.glass-button` | Win11 Secondary Button | 高度 32px，内边距 `0 16px`，圆角 4px，背景 `--win-control-alt`，1px `--win-stroke`，文字 `--win-text-primary` |
| `.glass-button-primary` | Win11 Primary Button | 背景 `--win-accent`，文字白色，圆角 4px，高度 32px |
| `.glass-button-danger` | Win11 Danger Button | 背景 `--win-danger`，文字白色，圆角 4px，高度 32px |
| `.glass-input` | Win11 TextBox | 高度 32px，圆角 4px，背景 `--win-control-alt`，1px `--win-stroke`，聚焦 2px `--win-accent` |
| `.glass-select` | Win11 ComboBox | 同 TextBox，下拉箭头用中性色 |
| `.glass-table` | Win11 Table | 表头 12px Regular，非大写；行高 40px；hover 背景 `--win-control-hover` |
| `.glass-progress-track` | Win11 ProgressBar | 高度 4px，背景 `--win-control-alt` |
| `.glass-progress-fill` | Win11 ProgressBar Fill | 背景 `--win-accent`，无流光 |
| `.glass-badge` | Win11 Status Text | 小号 12px，无彩色大底，仅文字/浅底 |
| `.glass-pill` | Win11 Toggle/Pill | 圆角 4px（或按场景保留胶囊），高度 32px |
| `.glass-stat` | Win11 Stat Card | 背景 `--win-layer`，圆角 8px，阴影 `--win-shadow-card` |
| `.code-surface` | Win11 Code Block | 深色中性底，保留等宽字体 |

### 2.4 验收标准

- [ ] `main.css` 中不再出现 `backdrop-filter`。
- [ ] `main.css` 中不再出现 iOS 大圆角（16/22/28px 作为主要控件圆角）。
- [ ] 所有颜色均引用 `--win-*` 变量或 Tailwind 中性色。
- [ ] 浅色/深色切换后全局观感符合 Win11 中性、克制风格。

---

## 3. 布局壳重做：`frontend/src/components/AppLayout.vue`

### 3.1 目标

将当前“浮动玻璃侧边栏 + 胶囊指示器”改为 Win11 NavigationView 风格。

### 3.2 具体改动

- 布局结构：
  - 移除外层 `gap: 12px; padding: 12px` 的浮动卡片布局。
  - 改为左右满高布局：左侧导航栏固定宽度，右侧内容区独立滚动。
  - 导航栏宽度：展开 `280px`（指南为 320px，可结合 1600px 窗口采用 280px 作为折中；如严格遵循则 320px）。
  - 导航栏背景：`var(--win-bg-base)` 或 `var(--win-layer)`，不透明，无大圆角。
- 导航项：
  - 高度 40px。
  - 圆角 4px。
  - 移除 `.nav-indicator` 滑动胶囊。
  - 选中态：背景 `--win-control-hover` + 左侧 4px `--win-accent` 竖条。
  - 悬停态：背景 `--win-control-hover`。
  - 按下态：背景 `--win-control-pressed`。
- 品牌区：
  - 高度 48px，左侧图标 + 标题。
  - 图标圆角 8px，尺寸 32px。
- 主题切换：
  - 保留功能，改为 Win11 风格按钮（32px 高，4px 圆角，中性背景）。
- 内容区：
  - 内边距 `24px`。
  - 背景 `--win-bg-base`，可让卡片透出。

### 3.3 可选的导航折叠

- 增加折叠按钮，展开 280px / 收起 48px。
- 收起时只显示图标，隐藏文字。
- 该功能为增强项，若时间不足可暂缓，但计划中保留。

### 3.4 验收标准

- [ ] 页面不再有“浮动玻璃侧边栏”视觉。
- [ ] 导航项选中态有左侧强调色竖条。
- [ ] 导航项高度 40px，圆角 4px。
- [ ] 内容区使用 24px 内边距。
- [ ] 深浅色下导航背景正确。

---

## 4. 通用组件重做

以下组件逐一处理，保留所有 props/events/逻辑。

### 4.1 `frontend/src/components/Icon.vue`

- 默认 `strokeWidth` 从 `1.6` 改为 `1.25`（接近 Segoe Fluent 的 1px @16px 视觉效果）。
- 保持 `stroke-linecap="round"`、`stroke-linejoin="round"`。
- 图标尺寸使用 16/20/24 体系。
- 若时间允许，将主要图标路径替换为更接近 Segoe Fluent Icons 的简化线条；至少保证视觉粗细统一。

### 4.2 `frontend/src/components/BookSelector.vue`

- `.glass-select` 自动获得新样式，无需大改。
- 按钮改为 Win11 Secondary Button。
- 删除 `ios-spinner` 的 iOS 风旋转动画，改为 Win11 简洁 loading（可选）。

### 4.3 `frontend/src/components/ProgressBar.vue`

- 使用 `.glass-progress-track/fill` 新样式。
- 进度条高度 4px，填充 `--win-accent`。
- 删除流光动画。

### 4.4 `frontend/src/components/LogConsole.vue`

- 保持深色代码控制台风格，但颜色改为 Win11 中性：
  - 背景 `#1F1F1F`（浅色主题下也保持深色，作为代码区）。
  - 边框 `#3D3D3D`。
  - 字体使用 `Cascadia Code` / `Consolas`。
- 日志行 hover 背景改为中性灰。

### 4.5 `frontend/src/components/TokenBadge.vue`

- 改为 Win11 轻量 badge：
  - 背景 `--win-control-alt`。
  - 文字 12px。
  - 彩色仅用于状态点或少量强调。

### 4.6 `frontend/src/components/ConfirmDialog.vue`

- 按 Win11 ContentDialog 规范：
  - 遮罩：`rgba(0,0,0,0.4)`。
  - 对话框：圆角 8px，背景 `--win-layer`，阴影 `--win-shadow-dialog`，内边距 24px。
  - 标题：20px Semibold。
  - 按钮区：右对齐，按钮间距 8px。
  - 按钮使用 Win11 Primary / Secondary。

### 4.7 `frontend/src/components/ChapterValue.vue`

- 卡片 `.cv-card` 改为 Win11 Card：
  - 圆角 8px，背景 `--win-layer`，边框 `--win-stroke`。
  - 字段 key 使用 `--win-text-secondary`，value 使用 `--win-text-primary`。
  - 嵌套列表缩进保持。

### 4.8 `frontend/src/components/ChapterDetailPanel.vue`

- 章节详情区域改为 Win11 面板。
- 控件（章节输入、模式选择）使用 Win11 TextBox/Select。
- 报告区保留 Markdown 样式，但颜色对齐 Win11。

### 4.9 验收标准

- [ ] 所有通用组件不再使用 iOS 玻璃/扫光/流光。
- [ ] 按钮、输入框、下拉框高度统一 32px，圆角 4px。
- [ ] 对话框符合 Win11 ContentDialog 规范。
- [ ] 图标视觉统一为细线、圆角端点。

---

## 5. 页面逐页重做

每个页面需要：
1. 替换模板中的旧 class（如 `glass-card` 继续可用但样式已变；新增 Win11 语义 class 则替换）。
2. 修改 scoped 样式中的圆角/颜色/阴影/间距。
3. 将内联 `style` 中硬编码的颜色/尺寸收敛到变量或 class。
4. 保持功能不变。

### 5.1 `frontend/src/pages/QueuePage.vue`

当前元素：队列表格、操作按钮、进度、日志、章节详情、Token 统计。

改动：
- 表格：Win11 Table，行高 40px，表头非大写，hover 中性灰。
- 状态徽章：使用 `--win-success/danger/warning/info` 浅底。
- 按钮：Primary/Danger/Secondary。
- 进度条：Win11 ProgressBar。
- 左侧日志/右侧详情布局保留，但卡片圆角改为 8px。
- 内联样式中的 `font-size: 10px`、`color: var(--color-system-red)` 等尽量收敛。

### 5.2 `frontend/src/pages/SettingsPage.vue`

当前元素：大量表单、下拉、开关、分类 chip、模型列表、日志。

改动：
- 输入框/下拉：Win11 TextBox/ComboBox。
- 开关：实现 Win11 ToggleSwitch 规范（40×20px，滑块 12px，开启态 `--win-accent`）。
- 分段控件：Win11 风格（4px 圆角，选中项 `--win-layer` + 阴影）。
- 分类 chip：Win11 轻量筛选 chip（4px 圆角，选中 `--win-accent-soft` + `--win-accent` 边框）。
- 表单间距：表单项纵向 24px，分组 48px。
- 错误提示：红色文字，不用大色块。

### 5.3 `frontend/src/pages/SplitterPage.vue`

当前元素：文件列表、分段控件、开关、表格、批量结果。

改动：
- 分段控件：Win11 SegmentedControl（4px 圆角，选中白底 + 阴影）。
- 开关：Win11 ToggleSwitch。
- 表格：Win11 Table。
- 状态文字：`st-pending/processing/done/error` 改用 `--win-*` 变量。

### 5.4 `frontend/src/pages/SummaryPage.vue`

当前元素：总结参数、进度步骤、报告 Markdown、聚合文件列表。

改动：
- 卡片：Win11 Card。
- 4 阶段进度条：改为 Win11 步骤条（40px 行高，4px 圆角，完成态 `--win-accent`，禁用态中性灰）。
- 报告 Markdown：`md-content` 样式对齐 Win11 排版（标题 20/28px，正文 14px）。
- 聚合文件按钮：Win11 Secondary Button。

### 5.5 `frontend/src/pages/WorkspacePage.vue`

当前元素：小说列表、归档列表、按钮。

改动：
- 列表行：40px 高，无边框大圆角，hover 中性灰。
- 卡片：Win11 Card。
- 按钮：Win11 Primary/Secondary/Danger。

### 5.6 `frontend/src/pages/PromptPreviewPage.vue`

当前元素：书目选择、输入上限、代码预览。

改动：
- 代码区：保留深色代码面，但颜色改为 Win11 中性（背景 `#1F1F1F`，语法色可保留绿/青）。
- 表单控件：Win11 TextBox/Button。

### 5.7 `frontend/src/pages/StylePage.vue`

当前元素：风格分析控制、结果、日志。

改动：
- 按钮、输入、徽章统一 Win11。
- 结果文本区域使用 Win11 排版。

### 5.8 `frontend/src/pages/StatsPage.vue`

当前元素：统计卡片、表格。

改动：
- 统计卡：Win11 Stat Card（8px 圆角，`--win-layer`，`--win-shadow-card`）。
- 表格：Win11 Table。
- 数字强调：使用 `--win-accent` 或中性黑，避免 iOS 多彩。

### 5.9 `frontend/src/pages/TimelinePage.vue`

当前元素：模式切换、筛选、时间线卡片、伏笔 chip。

改动：
- 模式切换：Win11 SegmentedControl。
- 时间线卡片：Win11 Card。
- 伏笔 chip：Win11 轻量筛选 chip。
- 颜色映射 `typeTints`：改为低饱和 Win11 色板，避免高饱和。

### 5.10 `frontend/src/pages/GraphPage.vue`

当前元素：SVG 关系图、图例、卡片。

改动：
- SVG 节点：圆形填充 `--win-layer`，描边 `--win-accent` 或中性灰。
- 边：`--win-stroke-strong`，透明度 0.6。
- 选中态：`--win-accent-soft` 填充 + `--win-accent` 描边。
- 图例/卡片：Win11 Card。

### 5.11 `frontend/src/pages/MapPage.vue`

当前元素：SVG 地图、地点树、关系列表。

改动：
- 地点节点：Win11 中性色 + 强调色选中。
- 树形/关系列表：40px 行高，hover 中性灰。
- 卡片：Win11 Card。

### 5.12 `frontend/src/pages/CharacterCardPage.vue`

当前元素：角色列表、角色卡、弧光/事件/关系。

改动：
- 角色列表：40px 行高，hover 中性灰。
- 角色卡：Win11 Card，8px 圆角。
- 统计块：Win11 Stat Card。
- 区块标题：Win11 Title 20px Semibold。

### 5.13 验收标准

- [ ] 12 个页面全部无 iOS 玻璃/极光/扫光/流光残留。
- [ ] 所有页面在浅色/深色下可读、不刺眼。
- [ ] 所有按钮、输入框、下拉框符合 Win11 尺寸/圆角。
- [ ] 表格、列表行高统一 40px（或至少视觉一致）。
- [ ] 内联 `style` 中的硬编码颜色/尺寸数量显著下降。

---

## 6. 动效与交互相应

### 6.1 动效时长

| 场景 | 时长 |
|---|---|
| 控件 hover/press | 150ms |
| 面板展开/收起 | 200ms |
| 页面切换 | 250ms |
| 对话框弹出 | 300ms |

统一使用 `--win-duration-*` 和 `--win-ease`。

### 6.2 需要移除的旧动效

- 按钮扫光 `.sweep`
- 进度条流光 `shimmer`
- 极光漂移 `aurora-drift`
- 按钮 hover 上浮 `translateY(-2px)`（改为背景色变化）
- 按钮 active `scale(0.97)`（改为背景变深 + 轻微下沉）

### 6.3 需要新增的动效

- 按钮 hover：背景 `--win-control-hover`
- 按钮 pressed：背景 `--win-control-pressed`，`translateY(0)`
- 列表/导航项 hover：背景色过渡
- 对话框：`opacity + scale(0.95→1)` 300ms
- 页面切换：`opacity + translateY(6px→0)` 250ms

### 6.4 可访问性

- 保留 `@media (prefers-reduced-motion: reduce)` 关闭动画。
- 焦点样式必须可见。

---

## 7. 暗色模式

- 所有颜色必须走 `html.dark` 变量。
- 当前 `html.dark` 中的深蓝玻璃色全部替换为 Win11 深色中性色：
  - 基底 `#202020`
  - 图层 `#323232`
  - 控件 `#2B2B2B`
  - 分割线 `#3D3D3D`
- 代码区、日志区在深浅色下保持一致（建议固定深色）。

---

## 8. WebView2 专项检查

- 全局搜索 `backdrop-filter`，必须为 0。
- 全局搜索 `-webkit-backdrop-filter`，必须为 0。
- 动画不使用 `background-position` 实现关键视觉（WebView2 不可靠）。
- 在 Windows WebView2 环境手动验证：
  - 窗口移动时背景不闪变。
  - 滚动时无残影。
  - 按钮 hover/按下流畅。
  - 对话框弹出/关闭正常。

---

## 9. 清理与收尾

### 9.1 删除/停用文件

- `frontend/src/sweep.ts`：如果不再需要扫光，从 `main.ts` 移除 `installSweep()`，并删除该文件。
- `frontend/scripts/ensure-backdrop.mjs`：已经是 no-op，可保留或删除；若删除需同步修改 `package.json` build script。
- `frontend/.bak_before_*`：属于历史备份，不提交。

### 9.2 需要更新的文件

- `frontend/src/main.ts`：移除 `installSweep` 导入和调用。
- `frontend/package.json`：如果删除 `ensure-backdrop.mjs`，将 build 改回 `vue-tsc -b && vite build`；否则保留。

### 9.3 代码质量

- 运行 `npx vue-tsc --noEmit` 或 `npm run build` 确保无类型错误。
- 运行 `npm run build` 确保生产构建通过。
- 如可能，运行 `npm run dev` 人工走查。

---

## 10. 执行顺序（里程碑）

| 阶段 | 内容 | 预估 |
|---|---|---|
| P0 | 备份当前前端（可复制 `frontend` 到 `frontend.bak_win11` 或依赖 git） | 0.5h |
| P1 | 重写 `main.css` 设计 token + 全局基类 | 1～2 天 |
| P2 | 重做 `AppLayout.vue` 导航壳 | 0.5～1 天 |
| P3 | 重做通用组件（9 个组件） | 1～2 天 |
| P4 | 重做 12 个页面 | 3～6 天 |
| P5 | 动效、暗色模式、WebView2 回归 | 1 天 |
| P6 | 清理、构建、全页面走查 | 1 天 |
| 合计 | | 7～13 人日 |

---

## 11. 每个文件的任务清单（可直接勾选）

### 全局

- [ ] `frontend/src/main.css`：重建 Win11 变量与全局样式
- [ ] `frontend/src/main.ts`：移除 `installSweep`
- [ ] `frontend/src/sweep.ts`：删除或停用
- [ ] `frontend/package.json`：同步 build script
- [ ] `frontend/index.html`：移除 `.app-aurora` 或改为 Win11 背景层

### 组件

- [ ] `frontend/src/components/AppLayout.vue`
- [ ] `frontend/src/components/Icon.vue`
- [ ] `frontend/src/components/BookSelector.vue`
- [ ] `frontend/src/components/ProgressBar.vue`
- [ ] `frontend/src/components/LogConsole.vue`
- [ ] `frontend/src/components/TokenBadge.vue`
- [ ] `frontend/src/components/ConfirmDialog.vue`
- [ ] `frontend/src/components/ChapterValue.vue`
- [ ] `frontend/src/components/ChapterDetailPanel.vue`

### 页面

- [ ] `frontend/src/pages/QueuePage.vue`
- [ ] `frontend/src/pages/SettingsPage.vue`
- [ ] `frontend/src/pages/SplitterPage.vue`
- [ ] `frontend/src/pages/SummaryPage.vue`
- [ ] `frontend/src/pages/WorkspacePage.vue`
- [ ] `frontend/src/pages/PromptPreviewPage.vue`
- [ ] `frontend/src/pages/StylePage.vue`
- [ ] `frontend/src/pages/StatsPage.vue`
- [ ] `frontend/src/pages/TimelinePage.vue`
- [ ] `frontend/src/pages/GraphPage.vue`
- [ ] `frontend/src/pages/MapPage.vue`
- [ ] `frontend/src/pages/CharacterCardPage.vue`

---

## 12. 验收总清单

- [ ] `grep -R "backdrop-filter" frontend/src` 无结果。
- [ ] `grep -R "sweep" frontend/src` 无结果（或确认已停用）。
- [ ] `npm run build` 通过。
- [ ] 浅色/深色主题均符合 Win11 中性、克制风格。
- [ ] 所有页面功能正常，无 JS 报错。
- [ ] 所有控件尺寸/圆角符合 Win11 指南。
- [ ] 强调色使用克制，界面不刺眼。
- [ ] 无 iOS 玻璃/极光/大圆角残留。

---

## 13. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 246 处内联样式导致风格不统一 | 中 | 分页处理，先收敛高频颜色/尺寸 |
| 17 个 scoped 样式块遗漏 | 中 | 按文件清单逐页检查 |
| 可视化页面 SVG 样式复杂 | 中 | 单独预留时间，先改颜色/卡片，再改布局 |
| WebView2 渲染差异 | 高 | 全程禁用 backdrop-filter，真实元素动画 |
| 改动量大导致回归 | 高 | 每阶段跑 `npm run build`，保留备份 |
| 设计方向偏差 | 中 | 先做 P1+P2+队列/设置样板，确认后再全量 |
