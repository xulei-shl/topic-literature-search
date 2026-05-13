# 维普 (VIP) 文献检索爬虫 - 技术分析与操作指南

## 一、项目概述

`vp-search` 是 `topic-literature-search` 项目中的三大中文文献检索模块之一，专门用于抓取维普期刊数据库 (https://qikan.cqvip.com/) 的学术文献数据。

### 项目位置
```
vp-search/scripts/
```

### 核心功能
- 高级检索（支持题名/关键词、摘要、年份范围、核心期刊筛选）
- 批量导出元数据（Excel 格式）和参考文献（TXT 格式）
- 断点续传（自动保存和恢复进度）
- 验证码处理和会话持久化
- 自动翻页和批量选择
- 数据处理（去重表头、合并批次、回填参考文献）

---

## 二、技术架构

### 2.1 核心技术栈

| 组件 | 用途 | 说明 |
|------|------|------|
| **Playwright** | 浏览器自动化 | 底层驱动框架 |
| **Camoufox** | 浏览器包装器 | 提供更好的反爬能力 |
| **Python 3.10+** | 运行环境 | 使用 dataclass、类型提示等现代特性 |
| **pandas/openpyxl** | Excel 处理 | 读取、清理、合并导出文件 |
| **win32com** | Windows COM 接口 | 兼容处理旧版 .xls 格式 |
| **logging** | 日志记录 | 结构化日志输出 |

### 2.2 架构设计模式

#### Mixin 多继承模式

```python
class VpSearchInteractor(
    VpFormMixin,              # 表单操作
    VpPageSizeMixin,          # 分页设置
    VpCheckboxListMixin,      # 复选框列表
    VpSelectionMixin,         # 结果选择
    VpNavigationMixin,        # 页面导航翻页
    VpPageMixin,              # 页面状态检测
    VpExportMixin,            # 导出操作
    VpProgressMixin,          # 进度管理
    VpYearlyExportMixin,      # 逐年导出
    BaseAdvancedExportFlow,   # 基础导出流程骨架
):
```

这种设计将不同职责的功能模块解耦，便于维护和复用。

#### 浏览器会话管理

`BrowserManager` 类负责：
- 启动/关闭 Camoufox 浏览器
- 持久化 cookies 和 localStorage
- 自动恢复登录会话
- 等待手动登录
- 检测验证码

#### 模板方法模式

`BaseAdvancedExportFlow` 定义了高级检索导出的骨架流程：

```
打开高级检索页 → 填写查询条件 → 提交搜索 → 等待结果 → 
批量选择 → 循环导出批次 → 合并文件 → 清理数据
```

各站点通过覆写钩子方法 (`_fill_advanced_search_form_from_params`、`_export_selected_results_for_batch` 等) 定制具体行为。

---

## 三、核心模块详解

### 3.1 BrowserManager (browser.py:21-293)

**职责**：浏览器生命周期与会话管理

**主要方法**：
- `start()` — 启动浏览器，配置代理、语言、GeoIP
- `close()` — 清理浏览器资源
- `save_session()` — 保存 cookies/localStorage/state
- `load_cookies()` / `load_local_storage()` — 恢复会话
- `restore_session()` — 导航到目标页并恢复会话
- `wait_for_manual_login()` — 阻塞等待用户手动登录

**会话文件位置**：
```
.vp-search/session/
├── cookies.json
├── local_storage.json
└── state.json
```

### 3.2 VpSearchInteractor (vp_search_interactor.py:26-225)

**职责**：协调各 Mixin 执行完整的高级检索与导出流程

**核心属性**：
- `EXPORT_BATCH_SIZE = 500` — 每批导出 500 条（维普的限制）
- 结果复选框选择器集合 (`RESULT_CHECKBOX_SELECTORS`)
- 分页器选择器 (`RESULTS_PAGER_SELECTORS`)
- 导出入口选择器 (`EXPORT_ENTRY_SELECTORS`)

**主要方法**：
- `login()` — 手动登录并保存会话
- `advanced_search()` — 高级检索入口（根据参数选择逐年导出或批量导出）

### 3.3 表单操作 (vp_form_ops.py)

**`_fill_advanced_search_form()` (vp_form_ops.py:30-46)**
- 设置检索条件：题名/关键词 + 摘要（OR 逻辑）
- 设置年份范围
- 核心期刊筛选（取消"全部期刊"，勾选北大核心/EI/SCI等）

**`_set_advanced_condition()` (vp_form_ops.py:47-77)**
- 逐行配置检索条件
- 精确匹配选项（lay-value='1'）
- 逻辑关系（与/或/非）
- 字段类型选择（题名/关键词/摘要等）

### 3.4 导航与翻页 (vp_navigation_ops.py)

**翻页策略**（多重降级）：
1. **页码跳转** — 使用"到第 X 页"输入框直接跳转 (`_jump_to_results_page`)
2. **数字链接** — 点击具体的页码链接 (`_goto_results_page_by_link`)
3. **下一页按钮** — 点击"下一页" (`_goto_next_results_page`)

**状态检测** (`_has_results_state_changed`, vp_navigation_ops.py:338-342)：
通过比较 URL、页码、第一条结果标题三者判断页面是否已刷新。

**重试机制**：
- 最大重试次数：`NEXT_PAGE_MAX_RETRIES = 3`
- 重试间隔：`NEXT_PAGE_RETRY_DELAY = 1s`

### 3.5 结果选择 (vp_checkbox_list_ops.py & vp_selection_ops.py)

**逐页选择策略**：
1. 切换到每页 50 条（`_prefer_results_page_size()`）
2. 遍历当前页所有结果复选框
3. 逐个勾选（避免一次性选择导致的加载问题）
4. 处理"全选"复选框（仅当总数 ≤ 批次大小时使用）

**批量累计**：
如果总结果数超过批次大小，分多批次导出，每批次 500 条，自动累计选中状态。

### 3.6 导出操作 (vp_export_ops.py)

**导出流程**：

```
点击"导出题录" → 检测导出页打开 → 
选择导出格式（excel/abstract） → 
点击"导出"按钮 → 捕获下载事件 → 保存文件
```

**导出的两种模式**：
- **自动下载** (`_should_auto_download_by_export_type`)：切换 excel 标签时直接触发下载
- **确认下载** (`_capture_export_download_by_confirm`)：需点击"导出"按钮

**文件验证** (`_validate_export_download_kind`)：
- 元数据：扩展名必须是 `.xls/.xlsx/.xlsm`
- 参考文献：扩展名必须是 `.txt`

**双格式导出**：
每个批次同时下载：
- `vp-export.xls` — 元数据表格
- `vp-reference.txt` — 参考文献文本

### 3.7 结果解析 (result_parser.py:11-124)

**页面类型检测** (`detect_page_type`)：
- `results` — 结果列表页（存在分页器或 `.search-result`）
- `advanced_search` — 高级检索页（存在 `advSearchKeywords` 输入框）
- `vp` — 维普域名下的其他页面
- `unknown` — 未知类型

**结果概要提取** (`parse_results_summary`)：
- 当前页码 / 总页数
- 总结果数 (`#hidShowTotalCount` 的 value)
- 是否存在下一页（检查 `.layui-laypage-next` 是否禁用）

### 3.8 导出后处理 (export_processor.py)

**数据清理** (`sanitize_export_excel`)：
1. 读取原始 Excel（支持 `.xlsx` / 旧版 `.xls`）
2. 识别并移除重复表头行（`_is_duplicate_header_row`）
3. 规范化单元格值（去除多余空白）
4. 重新保存为标准 xlsx

**参考文献解析** (`parse_reference_txt`)：
正则匹配 `^\[(\d+)\]` 分隔每条参考文献，合并换行。

**批次合并** (`enrich_batch_excel` + `merge_batch_excels`)：
1. 为单个批次 Excel 添加 `参考格式` 列（来自 TXT）
2. 将所有批次 Excel 垂直合并
3. 生成最终汇总文件 `{timestamp}-{query}-merged.xlsx`

### 3.9 进度持久化 (progress_store.py)

**存储内容**：
```
.vp-search/progress/{query_slug}.json
```
- 检索参数（query、date_from、date_to、core_only、max_download）
- 已导出批次索引
- 当前页码和行偏移
- 已生成批次文件列表
- 最终文件路径
- 错误信息

**恢复机制** (`BaseAdvancedExportFlow`)：
启动时读取进度文件，判断：
- 是否需要重新搜索（查询条件变化）
- 从哪一页继续翻页
- 从哪个批次索引继续导出

### 3.10 异常体系 (exceptions.py)

```
VpSearchError
├── BrowserError        # 浏览器启动/操作失败
├── ValidationError     # 参数或页面元素校验失败
├── CaptchaError        # 验证码未完成
├── TimeoutError        # 等待超时
├── NavigationStateError# 导航到目标页失败
├── ParseError          # 解析页面失败
├── UnsupportedPageError# 页面类型不支持
└── ExportProcessingError # 导出处理异常
```

---

## 四、网站特性与选择器

### 4.1 目标网站

**维普期刊数据库**：https://qikan.cqvip.com/

### 4.2 前端框架

维普使用 **Layui** 组件库，页面结构特征：
- 下拉框：`.layui-form-select` → `dd[lay-value='...']`
- 分页器：`.layui-laypage`（`.layui-laypage-curr`、`.layui-laypage-next`）
- 弹窗：`.layui-layer-dialog`
- 标签页：`#dateType li[data-type='...']`

### 4.3 关键选择器

| 用途 | 选择器 |
|------|--------|
| 高级检索入口 | `a:has-text('高级检索')`、`a[href*='/Qikan/Search/Advance']` |
| 检索词输入框 | `input[name='advSearchKeywords']` |
| 年份下拉 | `#basic_beginYear`、`#basic_endYear` |
| 核心期刊复选框 | `input[name='basic_journalRange'][title='北大核心期刊']` |
| 提交检索 | `button.behavior-advancesearch` |
| 结果复选框 | `input[name='selectArticle']`、`input[data-name='selectArticle']` |
| 全选复选框 | `input[name='selectArticleAll']` |
| 分页输入框 | `.layui-laypage-skip input.layui-input` |
| 分页确认按钮 | `.layui-laypage-skip button.layui-laypage-btn` |
| 下一页链接 | `.layui-laypage-next` |
| 导出题录入口 | `a.behavior-exporttitle`、`a:has-text('导出题录')` |
| 导出格式选择 | `#dateType li[data-type='excel']`、`li[data-type='abstract']` |
| 导出确认按钮 | `#exportbtn` |
| 导出页标识 | `li[data-type='excel']`（可见即表示就绪） |

---

## 五、操作步骤说明

### 5.1 环境准备

#### 依赖安装

```bash
# 进入项目目录
cd F:\Github\topic-literature-search

# 安装 Python 依赖（requirements.txt）
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium

# 首次运行会自动安装 Camoufox 扩展，或手动安装：
# camoufox install
```

**requirements.txt 关键依赖**：
```
playwright>=1.41.0
camoufox>=0.1.0
pandas>=2.0.0
openpyxl>=3.1.0
win32com>=221  # Windows 专用
```

#### 配置文件

环境变量（可选，在 `.env` 或命令行设置）：
```
# 代理（如需）
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890

# 调试日志
LOG_LEVEL=DEBUG
```

### 5.2 会话登录（一次性配置）

维普需要登录才能查看和导出完整结果。首次使用需手动登录：

```bash
# 进入维普子模块目录
cd vp-search

# 执行登录命令（浏览器会打开）
python scripts/cli.py login --headless false

# 在打开的浏览器窗口中：
# 1. 扫描二维码或输入账号密码登录
# 2. 登录完成后，回到终端按回车
# 3. 会话信息会自动保存到 .vp-search/session/
```

**会话文件**：
```
.vp-search/
└── session/
    ├── cookies.json
    ├── local_storage.json
    └── state.json
```

后续检索会自动复用此会话，无需重复登录。

### 5.3 高级检索（单次执行）

#### 基本用法

```bash
# 切换到维普模块
cd vp-search

# 执行高级检索（导出前 100 条）
python scripts/cli.py advanced-search \
    --query "人工智能" \
    --date-from 2020 \
    --date-to 2024 \
    --core \
    -n 100 \
    --output-dir ./outputs
```

#### 参数详解

| 参数 | 说明 | 示例 |
|------|------|------|
| `--query` | 检索词（题名或关键词） | `--query "机器学习"` |
| `--date-from` | 起始年份（YYYY） | `--date-from 2022` |
| `--date-to` | 结束年份（YYYY） | `--date-to 2024` |
| `--core` | 仅核心期刊（北大核心/EI/SCI等） | `--core` |
| `-n/--max-download` | 最多导出条数（默认全部） | `-n 500` |
| `--output-dir` | 输出目录（默认 `outputs/`） | `--output-dir ./results` |
| `--headless` | 无头模式（不显示浏览器） | `--headless` |
| `--proxy` | 代理地址 | `--proxy http://127.0.0.1:7890` |
| `--no-proxy` | 禁用代理 | `--no-proxy` |
| `--no-save` | 不保存 JSON 元数据 | `--no-save` |
| `--json-only` | 仅输出 JSON 到 stdout | `--json-only` |
| `--debug` | 开启 DEBUG 日志 | `--debug` |
| `--progress-file` | 指定断点续传进度文件 | `--progress-file ./my-progress.json` |

#### 检索逻辑

维普高级检索默认采用 **双字段 OR 检索**：
```
(题名或关键词包含 "人工智能") OR (摘要包含 "人工智能")
```

如需更复杂的检索式，需修改 `vp_form_ops.py::_fill_advanced_search_form()`。

#### 导出逻辑

1. **结果选择**：逐页勾选结果复选框
2. **批量分组**：每 500 条为一个批次（维普单次导出上限）
3. **导出格式**：每个批次导出两个文件
   - `*.xls` — 元数据（标题、作者、期刊、摘要等）
   - `*.txt` — 参考文献格式
4. **批次合并**：所有批次完成后自动合并为 `{timestamp}-{query}-merged.xlsx`

**输出目录结构**：
```
outputs/人工智能/
├── 20260511-203000-人工智能-abstract-batch001.txt
├── 20260511-203000-人工智能-abstract-batch002.txt
├── 20260511-203000-人工智能-metadata-batch001.xls
├── 20260511-203000-人工智能-metadata-batch002.xls
├── 20260511-203000-人工智能-merged.xlsx        # 最终合并文件
├── 20260511-203000-人工智能-report.json        # 汇总报告
└── .vp-search/
    └── progress/
        └── 人工智能.json                        # 断点进度
```

### 5.4 逐年导出模式

当检索时间跨度较大（如 1990-2024）且结果总数很多时，系统会自动切换到 **逐年导出模式**，避免单次检索超时或导出失败。

**触发条件**（`vp_search_interactor.py:152`）：
```python
def _should_use_yearly_full_export(date_to, max_download):
    year_span = int(date_to or datetime.now().year) - (date_from or 0)
    return year_span > 5 or (max_download and max_download > 5000)
```

**逐年导出流程**：
1. 按年份区间（如每 3 年一组）分批检索
2. 每批次独立执行完整的高级检索 + 导出流程
3. 记录空结果年份和跳过年份
4. 最终合并所有年份的批次文件

**启用参数**：
```bash
python scripts/cli.py advanced-search \
    --query "新青年" \
    --date-from 1915 \
    --date-to 1925  # 跨 10 年，会自动使用逐年导出
```

### 5.5 断点续传

**自动保存进度**：
- 每次成功导出批次后保存
- 意外中断后可以恢复

**恢复方式**：
```bash
# 直接重新执行相同命令（自动检测进度文件）
python scripts/cli.py advanced-search \
    --query "人工智能" \
    --date-from 2020 \
    --date-to 2024 \
    -n 1000

# 输出会显示：
#   resumed_from_progress: true
#   从中断处继续翻页和导出
```

**手动指定进度文件**：
```bash
python scripts/cli.py advanced-search \
    --query "人工智能" \
    --progress-file ./my-custom-progress.json
```

**清理进度**：
删除 `outputs/{query}/.vp-search/progress/` 下的 JSON 文件即可重新开始。

### 5.6 错误处理与重试

#### 自动重试机制

```python
# 翻页重试配置
NEXT_PAGE_MAX_RETRIES = 3       # vp_search_interactor.py:47
NEXT_PAGE_RETRY_DELAY = 1       # 重试间隔 1 秒

# 导出验证重试
export_processor 中下载失败自动重试 2 次
```

#### 常见错误与对策

| 错误类型 | 表现 | 处理方式 |
|---------|------|---------|
| `BrowserError` | 浏览器启动失败 | 检查 Camoufox 安装、显示模式 (`--headless false`) |
| `CaptchaError` | 验证码出现 | 手动完成验证，程序会暂停等待 |
| `TimeoutError` | 页面加载超时 | 增加 `--page-timeout` 或检查网络 |
| `ValidationError` | 页面元素找不到 | 网站改版导致选择器失效，需更新选择器 |
| `ExportProcessingError` | 导出文件无效 | 检查磁盘空间、文件权限 |

#### 调试模式

```bash
# 开启详细日志
python scripts/cli.py advanced-search \
    --query "测试" \
    --debug

# 查看完整 Playwright 跟踪
# 设置环境变量
export PLAYWRIGHT_DEBUG=1  # Linux/Mac
# Windows:
set PLAYWRIGHT_DEBUG=1
```

日志输出包含：
- 浏览器启动参数
- 页面 URL 变化
- 元素选择器匹配结果
- 下载文件路径
- 时间统计（导出耗时等）

### 5.7 无头模式与代理

#### 无头模式

```bash
# 后台运行（不显示浏览器窗口）
python scripts/cli.py advanced-search --query "测试" --headless

# 显示浏览器（调试用）
python scripts/cli.py advanced-search --query "测试" --headless false
```

#### 代理配置

```bash
# 使用系统代理
python scripts/cli.py advanced-search --query "测试"

# 指定代理
python scripts/cli.py advanced-search \
    --query "测试" \
    --proxy http://127.0.0.1:7890

# 禁用代理
python scripts/cli.py advanced-search --query "测试" --no-proxy
```

**代理环境变量**（优先级更高）：
```bash
# 提前设置
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
```

### 5.8 通过顶层入口并发执行

项目根目录的 `cli.py` 支持并发调用多个数据库：

```bash
# 回到项目根目录
cd F:\Github\topic-literature-search

# 并发检索维普 + 万方
python cli.py advanced-search --vp --wanfang --query "人工智能" -n 200

# 并发检索全部三个库
python cli.py advanced-search --all --query "人工智能" --date-from 2020

# 仅维普
python cli.py advanced-search --vp --query "人工智能"
```

**并发行为**：
- 三个库在独立的进程中并行执行
- 任一失败不影响其他任务
- 最终返回码：全部成功返回 `0`，任一失败返回 `1`

### 5.9 合并与清理（后续处理）

#### 合并多个数据库结果

```bash
# 合并维普 + 万方
python cli.py merge-excel --query "人工智能" --vp --wanfang

# 合并全部三个库
python cli.py merge-excel --query "人工智能" --all
```

**预处理**：
1. 繁体转简体
2. 清理参考文献序号（`[1]` → 空）
3. 添加来源标记列（`检索数据库`）

**去重**：

```bash
# 对合并结果去重（基于"题名"列）
python cli.py clean-excel --query "人工智能"
```

**LLM 相关性过滤**（待开发）：
```bash
python cli.py llm-filter --query "人工智能"
```

---

## 六、关键技术细节

### 6.1 页面就绪检测

避免在页面未加载完成时操作元素。

```python
def _wait_for_results_ready(self):
    deadline = time.time() + self.config.page_timeout
    while time.time() < deadline:
        if self._is_results_page_ready():
            return
        if self.page.locator(".no-data, .empty, .none-data").count() > 0:
            return  # 无结果也视为就绪
        time.sleep(0.5)
    raise TimeoutError("等待结果页超时")
```

**就绪标识选择器** (`vp_search_interactor.py:66-74`)：
- `#headerpager` / `#footerpager`（分页器）
- `#hidShowTotalCount`（总数隐藏域）
- `span.selected-count`（已选数量）
- `.checked-tip`（选中提示）
- `input[name='selectArticleAll']`（全选复选框）

### 6.2 下拉框操作

维普使用 Layui 自定义下拉，非原生 `<select>`。操作步骤：
1. 点击下拉容器 `.layui-form-select` 展开
2. 等待 `dd[lay-value='...']` 可见
3. 点击目标选项

```python
def _select_dropdown_option(self, root, option_value, display_text):
    dropdowns = root.locator(".layui-form-select")
    for dropdown in dropdowns.all():
        dropdown.click()
        option = dropdown.locator(f"dd[lay-value='{option_value}']").first
        if option.count() == 0:
            option = dropdown.locator("dd").filter(has_text=display_text).first
        option.click(force=True)
        return
```

### 6.3 导出页处理

维普的"导出题录"会打开一个 **新页面**（而非弹窗）。处理流程：
1. 记录当前页面的 `existing_pages`
2. 点击导出入口
3. 轮询检测新页面 (`_find_ready_export_page`)
4. 在新页面选择导出格式
5. 捕获 `expect_download()` 事件
6. 关闭导出页（或返回结果页）

**关键代码** (vp_export_ops.py:90-109):
```python
def _open_export_page(self, existing_pages, previous_url):
    deadline = time.time() + effective_timeout
    while time.time() < deadline:
        export_page = self._find_ready_export_page(existing_pages, previous_url)
        if export_page is not None:
            return export_page
        time.sleep(self._page_change_poll_interval_seconds())
```

### 6.4 Excel 读取兼容性

维普导出的 `.xls` 文件实际上是 **HTML 表格**（通过浏览器下载），而非标准二进制 XLS。

处理策略 (export_processor.py:116-124):
```python
def _read_legacy_excel(self, excel_path):
    readers = [self._read_html_table, self._read_excel_via_com]
    for reader in readers:
        try:
            return reader(excel_path).fillna("")
        except Exception as exc:
            errors.append(f"{reader.__name__}: {exc}")
```

- **首选**：`pd.read_html()` 解析 HTML 表格
- **兜底**：通过 `win32com` 启动 Excel 应用程序另存为 xlsx（Windows 专属）

### 6.5 验证码处理

当前实现中，验证码检测仅返回 `False` (browser.py:200-208)，表示 **不主动识别**，而是依赖：
1. 会话复用（避免频繁触发验证码）
2. 手动干预（控制台提示或等待）
3. 重试逻辑中调用 `_ensure_captcha_cleared()` 检查

如需自动打码，需集成打码平台或 OCR 服务。

### 6.6 逐年导出优化

`VpYearlyExportMixin` 实现按年份分段检索：
1. 计算年份跨度：`year_span = date_to - date_from`
2. 如果跨度 > 5 年，按 3 年一段分割
3. 每段独立执行高级检索 + 导出
4. 合并所有段的结果

**优势**：
- 避免单次检索结果过多（维普最多显示 5000 条）
- 防止导出超时或中断后需从头开始
- 更容易定位哪一年没有结果

---

## 七、常见问题与排错

### Q1: 浏览器启动失败

**错误**：`BrowserError: 浏览器启动失败`

**可能原因**：
- Camoufox 未正确安装
- Playwright 浏览器未安装
- 无头模式下缺少显示服务器（Linux）

**解决方案**：
```bash
# 1. 安装 Playwright 浏览器
playwright install chromium

# 2. 测试浏览器启动（显示模式）
python scripts/cli.py advanced-search --query "测试" --headless false

# 3. 若仍失败，查看详细日志
python scripts/cli.py advanced-search --query "测试" --debug
```

### Q2: 无法找到检索词输入框

**错误**：`ValidationError: 未找到第 1 条检索词输入框`

**原因**：维普页面改版，选择器 `input[name='advSearchKeywords']` 失效。

**诊断**：
```bash
# 开启调试，查看页面快照
python scripts/cli.py advanced-search --query "测试" --debug
# 查看浏览器控制台，检查元素 DOM 结构
```

**修复**：更新 `vp_form_ops.py:74` 的选择器为新的元素选择器。

### Q3: 导出文件为空或格式错误

**错误**：`ExportProcessingError: Excel 导出未生效`

**可能原因**：
- 导出页未完全加载就点击确认
- 导出格式选择错误（点了"文摘"而非"表格"）

**解决**：
1. 增加页面等待超时：
   ```bash
   python scripts/cli.py advanced-search --query "测试" --page-timeout 60
   ```
2. 检查导出页截图（`--debug` 模式下保存）
3. 验证 `EXPORT_PAGE_READY_SELECTORS` 是否正确定位到格式选择区

### Q4: 翻页失败

**错误**：`TimeoutError: 结果页翻页失败`

**原因**：
- 网络延迟导致页面加载慢
- 验证码阻塞
- 分页器结构变化

**排查**：
1. 使用 `--headless false` 观察翻页过程
2. 查看日志中的 `_has_results_state_changed` 是否返回 True
3. 检查 `_find_next_page_link()` 是否能定位到 `.layui-laypage-next`

### Q5: 导出条数少于预期

**现象**：计划导出 1000 条，实际只导出 300 条

**原因**：
- 维普单次检索最多返回 5000 条
- 结果筛选条件过于严格（如核心期刊）
- 某些年份无结果

**诊断**：
```bash
# 查看 parser 提取的总数
# 日志中会输出：total: 300
```

**解决**：
- 放宽筛选条件
- 使用逐年导出模式
- 检查检索式逻辑（OR/AND 设置）

### Q6: 会话失效，每次都要登录

**检查**：
1. 会话目录是否存在：`.vp-search/session/`
2. cookies.json 是否被覆盖或删除
3. 登录后是否正常退出（`browser_manager.close()` 会保存会话）

**强制重新登录**：
```bash
# 删除旧会话
rm -rf .vp-search/session/
# 重新登录
python scripts/cli.py login --headless false
```

### Q7: Windows 上读取 .xls 失败

**错误**：`ExportProcessingError: 读取 xls 失败`

**原因**：
- 文件被占用
- win32com 未安装或注册失败

**替代方案**：
```bash
# 强制使用 HTML 解析
# export_processor.py 优先尝试 pd.read_html()
# 确保已安装 lxml
pip install lxml
```

### Q8: 并发执行时文件冲突

**错误**：多个进程同时写入 `outputs/` 目录

**解决**：
- 为每个查询指定不同 `--output-dir`
- 或使用不同 `--progress-file` 避免共享进度文件

### Q9: 代理无法正常工作

**错误**：浏览器无法访问目标网站

**排查**：
```bash
# 1. 测试代理连通性
curl -x http://127.0.0.1:7890 https://qikan.cqvip.com/

# 2. 不使用代理
python scripts/cli.py advanced-search --query "测试" --no-proxy

# 3. 检查环境变量
echo $HTTP_PROXY
```

### Q10: 内存不足

**现象**：导出大量数据时程序崩溃

**原因**：
- pandas 一次性加载多个大 Excel
- 批次过多未及时合并释放

**优化**：
- 减少批次大小（修改 `EXPORT_BATCH_SIZE`）
- 在合并完成后删除临时批次文件（需修改代码）
- 增加系统虚拟内存

---

## 八、自定义扩展

### 8.1 修改导出字段

维普导出的字段由网站后端决定，前端无法增减。如需调整字段顺序或重命名，需在 `export_processor.py` 的 `_sanitize_export_dataframe()` 中处理。

### 8.2 添加新的检索字段

修改 `vp_form_ops.py::_fill_advanced_search_form()`，增加新的条件行：

```python
def _fill_advanced_search_form(self, query, date_from, date_to, core_only):
    # 默认：题名或关键词 + 摘要
    self._set_advanced_condition(0, "题名或关键词", query)
    self._set_advanced_condition(1, "摘要", query, logic="OR")
    
    # 新增：作者字段
    self._set_advanced_condition(2, "作者", "张三", logic="AND")
```

### 8.3 更换分页大小

维普支持每页 20/50/100 条。代码中强制切换为 50 条：

```python
PREFERRED_RESULTS_PAGE_SIZE = 50  # vp_search_interactor.py:46

def _prefer_results_page_size(self):
    self._set_native_select_value("#selectPageSize", "50")
```

如需改为 100 条，修改常量为 `100`。

### 8.4 自定义输出文件名

修改 `utils.py::build_output_slug()` 或 `export_processor.py::_build_batch_file_name()`。

示例：按年份命名批次
```python
def _build_batch_file_name(self, query, batch_index, kind, suffix):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = build_output_slug(query)[:40]
    year = self._current_search_year or "all"  # 需要自行记录当前年份
    return f"{timestamp}-{slug}-{year}-batch{batch_index:03d}{suffix}"
```

---

## 九、性能调优建议

### 9.1 网络加速

- 使用国内代理或 VPN 减少延迟
- 避免跨国访问维普（境外 IP 可能触发验证码）
- 调整超时参数（`page_timeout` 默认 30s，可增加至 60s）

### 9.2 并发控制

目前是单线程顺序翻页和导出。如需加速：
- **多开浏览器**：并发运行多个查询（不同关键词/年份区间）
- **异步并发**：需重构为异步 Playwright（工作量大，收益有限）

### 9.3 磁盘 I/O

导出大量批次时，建议：
- 使用 SSD 存储 `outputs/` 目录
- 定期清理旧批次（仅保留最终合并文件）
- 避免在网盘同步目录中运行

---

## 十、安全与合规

### 10.1 遵守 robots.txt

维普的 `robots.txt` 禁止自动化抓取。本项目仅用于 **个人学术研究**，请勿用于商业用途或大规模公开传播。

### 10.2 速率限制

代码中已有自然的延迟（翻页等待 0.5s 轮询、操作间隔 0.1-0.5s）。**不要**移除这些延迟，否则可能触发反爬。

### 10.3 数据 usage

导出的文献数据版权归维普和原文作者所有。仅限：
- 个人学习、研究
- 文本挖掘（需符合合理使用原则）
- 不得公开分享或商业化

---

## 十一、维护与更新

### 11.1 网站改版处理

维普前端可能随时更新，导致选择器失效。典型表现：
- `ValidationError: 未找到高级检索提交按钮`
- `TimeoutError: 等待结果页超时`

**诊断步骤**：
1. 用 `--headless false --debug` 启动
2. 观察浏览器中实际页面结构
3. 使用开发者工具检查元素，更新选择器常量

**需维护的选择器**：
- `vp_form_ops.py` - 表单元素
- `vp_navigation_ops.py` - 分页器
- `vp_export_ops.py` - 导出入口
- `vp_search_interactor.py` - 结果复选框

### 11.2 依赖升级

定期更新依赖以兼容新版浏览器：
```bash
pip install -U playwright camoufox pandas openpyxl
playwright install chromium
```

---

## 十二、快速参考

### 完整工作流示例

```bash
# 1. 登录（首次）
cd vp-search
python scripts/cli.py login --headless false

# 2. 检索并导出（逐年模式自动触发）
python scripts/cli.py advanced-search \
    --query "近代史" \
    --date-from 1910 \
    --date-to 1949 \
    --core \
    -n 2000 \
    --output-dir ./outputs

# 3. 等待完成，查看输出
ls outputs/近代史/
# 20260511-210000-近代史-merged.xlsx

# 4. 回到项目根目录，合并其他数据库
cd ../..
python cli.py merge-excel --query "近代史" --all
python cli.py clean-excel --query "近代史"
```

### 一行命令（推荐）

```bash
# 顶层入口一次性完成维普检索（输出到默认 outputs/ 目录）
python cli.py advanced-search --vp --query "人工智能" --date-from 2020 --date-to 2024 --core -n 500
```

---

## 文档版本

- **项目版本**：v2.0（Phase 2 - wiki 知识库编译模式）
- **文档生成时间**：2026-05-11
- **分析对象**：`vp-search/scripts/` 全模块
- **适用网站**：维普期刊数据库 (https://qikan.cqvip.com/)

---

**维护者**：研发效能总监 (Development Director)  
**遵循规范**：`.rules/00_STANDARDS.md`、`.rules/02_DEVELOPER.md`
