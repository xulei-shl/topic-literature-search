# vp-search

基于 Camoufox 的维普期刊高级检索与批量导出工具。

## 功能特性

- **高级检索**：支持多字段组合检索（题名、关键词、摘要）
- **日期范围筛选**：按发表年份区间过滤结果
- **核心来源过滤**：可选仅导出北大核心、EI、SCIE、CSCD、CSSCI 等来源期刊
- **批量导出**：自动分批导出 Excel 元数据和 TXT 参考文献格式
- **断点续跑**：支持进度文件恢复中断的导出任务
- **会话持久化**：自动保存登录状态、Cookies、LocalStorage

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方法

### 登录（首次）

```bash
python -m vp_search.scripts login
```

首次运行会启动浏览器，手动完成登录后按回车保存会话。

### 高级检索

```bash
python -m vp_search.scripts advanced-search --query "人工智能"
```

#### 选项

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--query` | 检索词 | 必填 |
| `--date-from` | 起始年份（YYYY） | 全部 |
| `--date-to` | 结束年份（YYYY） | 全部 |
| `--core` | 仅核心来源 | false |
| `-n, --max-download` | 最大导出条数 | 无限制 |
| `--progress-file` | 断点续跑进度文件 | 新建 |
| `-o, --output-dir` | 输出目录 | ./outputs/vp-search |
| `--headless` | 无头模式 | false |
| `--debug` | 调试日志 | false |

## 输出文件

```
outputs/vp-search/
├── 20240101-120000-ai-query-batch001-metadata.xls   # Excel 元数据
├── 20240101-120000-ai-query-batch001-reference.txt  # 参考文献
├── 20240101-120000-ai-query-batch002-metadata.xls
├── 20240101-120000-ai-query-batch002-reference.txt
└── progress.json  # 进度文件（用于断点续跑）
```

## 断点续跑

导出中断后，使用进度文件恢复：

```bash
python -m vp_search.scripts advanced-search \
  --query "人工智能" \
  --progress-file outputs/vp-search/progress.json
```

## 模块结构

```
vp-search/
├── data/session/          # 会话存储（cookies、localStorage）
└── scripts/
    ├── cli.py             # CLI 入口
    ├── browser.py         # 浏览器管理
    ├── config.py          # 配置定义
    ├── progress_store.py  # 进度存储
    ├── result_parser.py    # 结果解析
    ├── export_processor.py # 导出处理
    ├── vp_*.py            # 站点操作实现
    │   ├── vp_form_ops.py       # 表单操作
    │   ├── vp_export_ops.py    # 导出操作
    │   ├── vp_navigation_ops.py# 导航操作
    │   ├── vp_page_ops.py       # 分页操作
    │   ├── vp_page_size_ops.py  # 页面大小
    │   ├── vp_selection_ops.py  # 选择操作
    │   ├── vp_checkbox_list_ops.py # 复选框
    │   ├── vp_progress_ops.py  # 进度操作
    │
    └── exceptions.py      # 异常定义
```

## 继承关系

`VpSearchInteractor` 继承以下 Mixin 及基类：

- `VpFormMixin` - 表单填写
- `VpPageSizeMixin` - 页面大小切换
- `VpCheckboxListMixin` - 复选框操作
- `VpSelectionMixin` - 结果选择
- `VpNavigationMixin` - 导航与翻页
- `VpPageMixin` - 分页控制
- `VpExportMixin` - 批量导出
- `VpProgressMixin` - 进度管理
- `BaseAdvancedExportFlow` - 高级导出流程骨架

## 配置项

通过 `VpSearchConfig` 配置：

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `home_url` | 主页 URL | https://qikan.cqvip.com/ |
| `advanced_search_url` | 高级检索页 URL | https://qikan.cqvip.com/Qikan/Search/Advance |
| `headless` | 无头模式 | false |
| `geoip` | 启用 GeoIP | true |
| `proxy` | 代理地址 | None |
| `language` | 浏览器语言 | zh-CN |
| `output_dir` | 输出目录 | ./outputs/vp-search |

## 注意事项

1. 首次使用需手动登录一次，会话自动保存
2. 批量导出每批 500 条，超过自动分批
3. 导出过程请勿手动关闭浏览器
4. 断点续跑仅恢复导出进度，检索条件需重新指定