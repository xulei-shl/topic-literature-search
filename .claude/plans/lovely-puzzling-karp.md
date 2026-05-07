# 万方检索模块设计方案

## Context

项目已完成 CNKI 检索模块，采用 Mixin + BaseAdvancedExportFlow 模板方法架构，实现了高级检索、批量勾选、分页导航、分批导出（Excel+TXT）、断点续跑等完整流程。现需参照 CNKI 的架构模式，为万方数据库实现同构检索模块，复用 `src/` 下的共享基础设施，新代码存放于 `wanfang-search/`。

万方与 CNKI 的关键差异：
- 万方使用 iView UI 组件库（`ivu-select`、`ivu-checkbox`），交互模式与 CNKI 自定义下拉不同
- 日期范围使用下拉选择（年份列表）而非文本输入
- 导出流程：点击"批量引用"→ 新页面 → "自定义格式"→"列表格式"→"导出 XLS"；另走"参考文献"→"导出 TXT"
- 导出 XLS 首行首列含类型标签（如"期刊"），需清除
- 每页条数选择器位于结果页上方（20/30/50）

---

## 目录结构

```
wanfang-search/
├── scripts/
│   ├── __init__.py
│   ├── __main__.py              # 模块入口
│   ├── cli.py                   # CLI 参数解析与命令路由
│   ├── config.py                # 万方配置（继承 BaseSearchConfig）
│   ├── browser.py               # 浏览器生命周期与会话管理
│   ├── wanfang_search_interactor.py  # 主编排器（Mixin 组合）
│   ├── wanfang_form_ops.py      # 表单填写 Mixin
│   ├── wanfang_navigation_ops.py # 分页导航 Mixin
│   ├── wanfang_page_ops.py      # 页面等待与工具 Mixin
│   ├── wanfang_selection_ops.py # 结果勾选 Mixin
│   ├── wanfang_export_ops.py    # 导出操作 Mixin
│   ├── wanfang_progress_ops.py  # 进度管理 Mixin
│   ├── wanfang_public_ops.py    # 公共检索方法 Mixin
│   ├── result_parser.py         # 结果页解析
│   ├── export_processor.py      # Excel/TXT 后处理
│   ├── progress_store.py        # 进度文件存储
│   ├── exceptions.py            # 异常层次
│   └── utils.py                 # CLI 输出工具
├── data/
│   └── session/                 # 会话持久化
└── outputs/                     # 运行时输出
```

---

## 模块详细设计

### 1. config.py — 万方配置

```python
@dataclass
class WanfangSearchConfig(BaseSearchConfig):
    OUTPUT_NAMESPACE: ClassVar[str] = "wanfang-search"
    home_url: str = "https://www.wanfangdata.com.cn/"
    advanced_search_url: str = "https://s.wanfangdata.com.cn/advanced-search/paper"
```

**复用**: `BaseSearchConfig`（超时解析、输出目录、会话目录等）

### 2. exceptions.py — 异常层次

```
WanfangSearchError (base)
├── BrowserError
├── ValidationError
├── CaptchaError
├── TimeoutError
├── NoResultsError
├── ParseError
├── NavigationStateError
├── UnsupportedPageError
└── ExportProcessingError
```

与 CNKI 同构，基础异常名改为 `WanfangSearchError`。

### 3. browser.py — 浏览器管理

与 CNKI 的 `BrowserManager` 结构基本相同，关键差异：
- `is_captcha_visible()`: 万方验证码选择器待实际确认（初版暂用通用检测逻辑 + 日志埋点）
- `restore_session()`: 导航到 `home_url` 后恢复 cookies/localStorage
- 会话文件存储于 `wanfang-search/data/session/`

### 4. result_parser.py — 结果页解析

**核心方法**:

| 方法 | 职责 |
|------|------|
| `detect_page_type()` | 识别页面类型（results/advanced_search/unknown） |
| `parse_results_summary()` | 提取总数、当前页码、总页数 |
| `parse_results(limit)` | 解析结果列表 |

**万方选择器映射**:
- 总数: `span.total-number span.mark-number` → `7,618`
- 当前页/总页: `span.currentpage`（`1 /`）+ 父级 `span.page-number` 中文本
- 每页条数: `div.select-box span.cur-text` → `显示 20 条`

**结果行解析**（需实际页面确认选择器，初版用通用选择器 + 埋点日志）:
- 每行结果包含: 题名、作者、来源、日期、摘要片段等

### 5. wanfang_form_ops.py — 表单填写 Mixin

**核心方法**:

| 方法 | 职责 |
|------|------|
| `_open_advanced_search_page()` | 打开高级检索页 |
| `_fill_advanced_search_form_from_params(params)` | 填写表单（骨架要求） |
| `_fill_advanced_search_form(query, date_from, date_to)` | 万方表单填写 |
| `_submit_advanced_search()` | 点击检索按钮 |
| `_set_results_per_page(count)` | 设置每页显示条数 |

**万方表单交互逻辑**（基于 docs/万方.md）:

```
1. 打开高级检索页: page.goto(advanced_search_url)
2. 确保存在 2 条检索条件行
3. 第 1 行: 检索字段="主题", 输入=关键词
4. 第 2 行: 逻辑="或", 检索字段="关键词", 输入=关键词
5. 关闭"中英文扩展"（点击 active 状态的 span.resource-item）
6. 设置时间范围（两个 ivu-select 下拉: 开始年/结束年）
7. 点击"检索"按钮 (span.submit-btn)
```

**iView 下拉交互**（关键差异点）:
万方使用 `ivu-select` 组件，交互流程为：
1. 点击 `div.ivu-select-selection` 展开下拉
2. 等待 `ul.ivu-select-dropdown-list` 可见
3. 点击目标 `li.ivu-select-item`
4. 验证 `span.ivu-select-selected-value` 已变更

需封装通用方法 `_select_ivu_option(trigger_selector, option_text)` 并增加埋点日志记录每次选择操作。

### 6. wanfang_navigation_ops.py — 分页导航 Mixin

**核心方法**:

| 方法 | 职责 |
|------|------|
| `_restore_results_position(target_page)` | 恢复到目标页码 |
| `_goto_next_results_page()` | 点击"下一页" |
| `_goto_results_page_by_link(link)` | 点击数字页码 |
| `_has_results_state_changed(...)` | 检测结果页状态变化 |

**万方选择器映射**:
- 下一页: `span.next`
- 上一页: `span.prev`
- 页码: `span.pager`
- 当前页: `span.pager.active`

**与 CNKI 差异**:
- 万方分页器使用 `span` 元素，CNKI 使用 `a` 元素
- 翻页重试逻辑与 CNKI 一致（最多 3 次重试 + JS 兜底点击）

### 7. wanfang_selection_ops.py — 结果勾选 Mixin

**核心方法**:

| 方法 | 职责 |
|------|------|
| `_clear_selected_results()` | 清除已选（点击"清除"） |
| `_select_batch_results(export_limit, row_offset)` | 勾选批次结果 |
| `_ensure_checkbox_checked(checkbox)` | 稳定勾选复选框 |

**万方选择器映射**:
- 全选复选框: `div.top-check-bar div.wf-checkbox label.ivu-checkbox-wrapper`
- 单行复选框: 每行结果中的 `div.wf-checkbox` (ivu-checkbox)
- 已选数量: `span.checked-tip span.mark-number`
- 清除按钮: `span.clear-btn`

**ivu-checkbox 勾选**:
- 使用 `span.ivu-checkbox-inner` 的父 `label` 点击
- 验证 `span.ivu-checkbox` 是否包含 `ivu-checkbox-checked` class
- JS 兜底: 设置 checked 属性并触发 input/change/click 事件

### 8. wanfang_export_ops.py — 导出操作 Mixin

**核心方法**:

| 方法 | 职责 |
|------|------|
| `_export_selected_results_for_batch(query, batch_index, output_dir, batch_selection)` | 导出当前批次 |
| `_export_xls(export_page, output_dir, ...)` | 自定义格式 → 列表格式 → 导出 XLS |
| `_export_txt(export_page, output_dir, ...)` | 参考文献 → 导出 TXT |

**万方导出流程**（与 CNKI 差异最大）:

```
1. 点击"批量引用"按钮 (span.export-btn, text="批量引用")
2. 等待新页面打开（新标签页）
3. 在新页面中:
   a. 导出 XLS:
      - 点击"自定义格式" tab (div#tab-m5)
      - 选择"列表格式" radio (ivu-radio-group 第二项)
      - 点击"导出XLS" (div.export-text-action)
      - 等待下载完成
   b. 导出 TXT:
      - 点击"参考文献" tab (div#tab-m2)
      - 点击"导出TXT" (div.export-text-action)
      - 等待下载完成
4. 关闭导出页面
```

**关键差异**:
- CNKI: 在原页面点击"导出与分析"→"导出文献"→"自定义"→ 弹出新页
- 万方: 在原页面点击"批量引用"→ 弹出新页，新页内切换 tab
- 万方无个人账号登录弹框（初版不考虑，预留日志埋点）

### 9. export_processor.py — 导出后处理

**核心方法**:

| 方法 | 职责 |
|------|------|
| `sanitize_export_excel(excel_path, output_path)` | 清理 XLS（去类型标签行、规范化） |
| `parse_reference_txt(txt_path)` | 解析参考文献 TXT |
| `enrich_batch_excel(excel_path, txt_path, output_path)` | 回填参考格式列 |
| `merge_batch_excels(excel_paths, output_path)` | 合并批次文件 |

**万方 XLS 清理特殊逻辑**:
- 首行首列为类型标签（如"期刊"、"学位论文"），需删除该行
- 表头行: 序号、题名、作者、作者单位、刊名、ISSN、页码、摘要、关键词、DOI、CN、核心类型、中图分类号
- 与 CNKI 类似的分段表头处理逻辑，但需适配万方列名

**万方 TXT 解析**:
- 格式: `[1] 作者. 题名[J]. 刊名,年,卷(期):页码. DOI:xxx.`
- 与 CNKI 的 GB/T 格式类似，正则 `^\[(\d+)\]` 匹配编号

### 10. progress_store.py — 进度存储

```python
class SearchProgressStore(BaseSearchProgressStore):
    SEARCH_PARAM_KEYS = (
        "query",
        "date_from",
        "date_to",
        "max_download",
    )
    BOOLEAN_PARAM_DEFAULTS = {}
    FALLBACK_SLUG = "wanfang"
    VALIDATION_ERROR_CLASS = ValidationError
```

万方暂无 `core_only` 和 `include_no_fulltext` 参数。

### 11. wanfang_progress_ops.py — 进度管理 Mixin

与 CNKI 的 `CnkiProgressMixin` 同构，适配万方参数（去掉 core_only/include_no_fulltext）。

### 12. wanfang_page_ops.py — 页面工具 Mixin

与 CNKI 的 `CnkiPageMixin` 同构，关键差异：
- `_wait_for_results_ready()`: 等待万方结果列表加载（选择器待确认，初版用 `span.total-number` 或结果行）
- `_ensure_captcha_cleared()`: 万方验证码检测逻辑
- `_first_result_title()`: 万方结果首行标题选择器

### 13. wanfang_public_ops.py — 公共检索 Mixin

初版仅实现 `search()` 基础检索，暂不实现 `navigate_results`/`get_paper_detail`。

### 14. wanfang_search_interactor.py — 主编排器

```python
class WanfangSearchInteractor(
    WanfangPublicMixin,
    WanfangFormMixin,
    WanfangSelectionMixin,
    WanfangNavigationMixin,
    WanfangPageMixin,
    WanfangExportMixin,
    WanfangProgressMixin,
    BaseAdvancedExportFlow,
):
    ADVANCED_FORM_READY_SELECTORS = (
        "input.ivu-input.ivu-input-default",
        "span.submit-btn",
    )
    EXPORT_BATCH_SIZE = 500
    NEXT_PAGE_MAX_RETRIES = 3
    NEXT_PAGE_RETRY_DELAY = 1
```

### 15. cli.py — CLI 接口

```
wanfang-search login                   # 手动登录保存会话
wanfang-search advanced-search         # 高级检索批量导出
    --query KEYWORD                    # 检索词
    --date-from YYYY                   # 起始年份
    --date-to YYYY                     # 结束年份
    -n, --max-download N               # 最多导出条数
    --progress-file PATH               # 断点续跑进度文件
    --debug                            # 调试模式
```

与 CNKI CLI 对比：去掉 `--core`、`--include-no-fulltext`；保留通用参数（headless, proxy, output-dir 等）。

### 16. utils.py — CLI 输出

与 CNKI 的 `utils.py` 同构，`DEFAULT_RESULT_TYPE = "wanfang"`。

---

## 埋点日志策略（第一版重点）

为便于调试万方特有交互，在以下关键节点增加 **DEBUG 级别** 埋点日志：

| 模块 | 埋点位置 | 日志内容 |
|------|----------|----------|
| wanfang_form_ops | 每个 ivu-select 操作前后 | `选择器=%s, 目标值=%s, 操作前值=%s, 操作后值=%s` |
| wanfang_form_ops | 中英文扩展关闭 | `操作前class=%s, 操作后class=%s` |
| wanfang_form_ops | 检索按钮点击 | `按钮可见=%s, 当前URL=%s` |
| wanfang_navigation_ops | 翻页前后 | `当前页=%s, 目标页=%s, URL变化=%s→%s` |
| wanfang_selection_ops | 每个复选框勾选 | `行号=%s, 勾选前checked=%s, 勾选后checked=%s` |
| wanfang_selection_ops | 已选数量读取 | `DOM读取=%s, 期望=%s` |
| wanfang_export_ops | 导出页打开 | `新页面URL=%s, 标签数=%s` |
| wanfang_export_ops | Tab 切换 | `目标tab=%s, 切换后active=%s` |
| wanfang_export_ops | 下载触发 | `选择器=%s, 建议文件名=%s, 实际路径=%s` |
| result_parser | 结果页解析 | `总行数=%s, 总数=%s, 当前页=%s, 总页=%s` |
| export_processor | XLS 清理 | `原始行数=%s, 清理后行数=%s, 删除首行=%s` |
| browser | 会话恢复 | `cookies数=%s, localStorage项=%s, 目标URL=%s` |

所有日志使用中文，遵循 `src/utils/result_output.py` 的格式规范。

---

## 复用的共享组件清单

| 共享组件 | 路径 | 用途 |
|----------|------|------|
| `BaseAdvancedExportFlow` | `src/core/advanced_export_flow.py` | 批量导出主流程骨架 |
| `AdvancedExportResult` 等类型 | `src/core/advanced_export_types.py` | 统一返回结构 |
| `BaseSearchConfig` | `src/utils/search_config.py` | 配置基类 |
| `BaseSearchProgressStore` | `src/utils/progress_store.py` | 进度文件基类 |
| `playwright_page` 工具函数 | `src/utils/playwright_page.py` | 原子浏览器操作 |
| `result_output` 工具函数 | `src/utils/result_output.py` | 日志/JSON/文件名 |
| `search_timeout` | `src/utils/search_timeout.py` | 超时配置解析 |
| `cli_dates` | `src/utils/cli_dates.py` | 日期参数校验 |
| `output_path` | `src/utils/output_path.py` | 输出目录解析 |

---

## 实现步骤

### Step 1: 基础骨架
- 创建 `wanfang-search/scripts/` 目录
- 实现 `config.py`, `exceptions.py`, `utils.py`
- 实现 `__init__.py`, `__main__.py`

### Step 2: 浏览器管理
- 实现 `browser.py`（参照 CNKI，适配万方验证码检测）

### Step 3: 表单操作
- 实现 `wanfang_form_ops.py`（iView 下拉交互 + 埋点日志）
- 实现 `result_parser.py`（基础页面类型检测 + 结果摘要解析）

### Step 4: 页面工具 + 公共方法
- 实现 `wanfang_page_ops.py`
- 实现 `wanfang_public_ops.py`

### Step 5: 导航 + 选择
- 实现 `wanfang_navigation_ops.py`
- 实现 `wanfang_selection_ops.py`

### Step 6: 导出
- 实现 `wanfang_export_ops.py`
- 实现 `export_processor.py`

### Step 7: 进度 + 主编排器
- 实现 `progress_store.py`
- 实现 `wanfang_progress_ops.py`
- 实现 `wanfang_search_interactor.py`

### Step 8: CLI
- 实现 `cli.py`

### Step 9: 测试
- 在 `tests/` 下添加万方相关单元测试

### Step 10: 文档留痕
- 在 `docs/changelog/` 记录开发变更

---

## 验证方式

1. **单元测试**: `python -m pytest tests/ -k wanfang -v`
2. **手动端到端测试**:
   ```bash
   cd wanfang-search
   python -m scripts login                     # 先登录保存会话
   python -m scripts advanced-search --query "新青年" --date-from 2020 --debug  # 小规模测试
   ```
3. **验证点**:
   - 高级检索表单正确填写（2 条条件行、逻辑"或"、日期范围）
   - "中英文扩展"已关闭
   - 结果页总数、页码解析正确
   - 批次勾选数量与导出文件行数一致
   - XLS 首行类型标签已清除
   - TXT 参考文献格式正确解析
   - 最终合并文件完整
   - 断点续跑: 中断后使用 `--progress-file` 恢复
   - 埋点日志在 `--debug` 模式下完整输出
