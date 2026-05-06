# CNKI 检索脚本

基于 Camoufox + Playwright 的 CNKI 检索脚本。当前真实支持的能力只有三类：

- 手动登录并持久化会话
- 从知网首页执行普通检索并解析当前结果页
- 从知网首页进入新版高级检索，分批导出元数据与参考文献，并在本地合并为最终 Excel

## 文件结构

- `cli.py`：命令行入口
- `browser.py`：浏览器生命周期、cookies、localStorage、state 管理
- `interactor.py`：页面交互、翻页、导出流程
- `result_parser.py`：结果页与详情页解析
- `export_processor.py`：导出 Excel / TXT 的本地清理、回填、合并
- `config.py`：运行配置
- `utils.py`：日志、控制台输出、JSON 保存
- `exceptions.py`：异常定义

运行后会在 `cnki-search/data/session/` 下生成：

- `cookies.json`
- `local_storage.json`
- `state.json`

默认输出目录是项目根目录下的 `outputs/cnki-search/<检索词slug>/`。

## 依赖

至少需要：

- Python 3.10+
- `camoufox`
- `playwright`
- `pandas`
- `openpyxl`
- `pywin32`
- `lxml`

示例安装：

```bash
pip install camoufox playwright pandas openpyxl pywin32 lxml
```

如果高级导出时会触发个人登录弹框，还需要在仓库根目录 `.env` 中配置：

```env
CNKI_USERNAME=your_cnki_username
CNKI_PASSWORD=your_cnki_password
```

通用超时参数也支持在仓库根目录 `.env` 中配置，这组变量不带数据库前缀，可被当前已有的各数据库检索脚本复用：

```env
PAGE_TIMEOUT=30
NAVIGATION_TIMEOUT=30
CAPTCHA_TIMEOUT=120
ACTION_TIMEOUT=10
PAGE_CHANGE_TIMEOUT=90
```

## 命令总览

```bash
python cnki-search/scripts/cli.py --help
```

当前只有 3 个子命令：

- `login`
- `search`
- `advanced-search`

所有子命令共用的常用参数：

- `-o, --output-dir`：指定输出目录
- `--headless`：无头模式
- `--no-geoip`：关闭 GeoIP
- `--proxy`：指定代理
- `--no-proxy`：不使用默认代理
- `--language`：浏览器语言，默认 `zh-CN`
- `--no-save`：不保存 JSON 结果
- `--json-only`：只输出 JSON
- `--debug`：输出 DEBUG 日志

## 1. 登录

第一次建议先执行：

```bash
python cnki-search/scripts/cli.py login
```

脚本会：

1. 启动 Camoufox 浏览器
2. 打开知网首页
3. 等待你手动完成登录
4. 在终端按回车后保存 cookies、localStorage 和页面状态

后续 `search` / `advanced-search` 会尝试复用这份登录态。

## 2. 普通检索

```bash
python cnki-search/scripts/cli.py search "大模型 教育"
```

行为说明：

- 从知网首页输入关键词
- 提交检索
- 等待结果页稳定
- 解析当前页结果
- 输出可读结果并保存 JSON

常用参数：

- `-n, --limit`：只返回当前页前 N 条结果

示例：

```bash
python cnki-search/scripts/cli.py search "人工智能"
python cnki-search/scripts/cli.py search "知识图谱" -n 20 --json-only --no-save
```

输出字段通常包含：

- `result_type`
- `query`
- `total`
- `page`
- `current_page`
- `total_pages`
- `has_next_page`
- `sort_by`
- `url`
- `results`

单条结果通常包含：

- `n`
- `title`
- `href`
- `export_id`
- `authors`
- `journal`
- `date`
- `database`
- `citations`
- `downloads`
- `is_online_first`

## 3. 高级检索与批量导出

```bash
python cnki-search/scripts/cli.py advanced-search --query "AI 知识服务" -n 100
```

### 实际流程

当前代码的高级检索流程是固定的：

1. 回到知网首页
2. 点击首页 `高级检索`
3. 再切换到统一新版高级检索页 `https://kns.cnki.net/kns8s/AdvSearch?classid=YSTT4HG0`
4. 取消勾选 `中英文扩展`
5. 固定使用两条检索条件
   - 第 1 条：`主题 = query`
   - 第 2 条：`篇关摘 = query`
   - 两条条件之间逻辑为 `OR`
6. 可选设置起始年份
7. 可选设置结束年份
8. 默认保留 `仅看有全文`
9. 传入参数后可取消 `仅看有全文`
10. 可选勾选核心来源
11. 提交检索
12. 按最多 `500` 条一批执行导出

### 参数

- `--query`：检索词。新任务启动时必填；通过进度文件恢复时可省略
- `--date-from`：起始年份，只支持 `YYYY`
- `--date-to`：结束年份，只支持 `YYYY`
- `--include-no-fulltext`：取消默认勾选的 `仅看有全文`
- `--core`：取消 `全部期刊`，并勾选 `WJCI / SCI / EI / 北大核心 / CSSCI / CSCD / AMI`
- `-n, --max-download, --max-export`：总导出上限
- `--progress-file`：指定断点续跑进度文件；恢复时可直接只传这个参数

示例：

```bash
python cnki-search/scripts/cli.py advanced-search --query "AI 知识服务" -n 100
python cnki-search/scripts/cli.py advanced-search --query "人工智能" --date-from 2020 --date-to 2025 --core -n 500
python cnki-search/scripts/cli.py advanced-search --query "数字图书馆" --date-to 2024 --include-no-fulltext -n 150
python cnki-search/scripts/cli.py advanced-search --query "新青年" --include-no-fulltext --date-to 2025 --progress-file "outputs/cnki-search/新青年/progress-新青年-xxxx.json"
```

### `--core` 的实际行为

传入 `--core` 后，脚本会在新版高级检索页中执行：

1. 取消勾选 `全部期刊`
2. 勾选 `WJCI`
3. 勾选 `SCI来源期刊`
4. 勾选 `EI来源期刊`
5. 勾选 `北大核心`
6. 勾选 `CSSCI`
7. 勾选 `CSCD`
8. 勾选 `AMI`

如果不传 `--core`，则不会主动修改这些来源类别复选框。

### 导出链路

每一批导出时，脚本会执行：

1. 结果页勾选文献
2. 进入 `导出与分析 -> 导出文献 -> 自定义`
3. 下载元数据文件
4. 下载 `GB/T 7714-2015 格式引文` 的 TXT
5. 本地清理元数据文件
6. 将 TXT 中的参考文献按顺序回填到 Excel 的 `参考格式` 列
7. 多批次时最终合并为一个总 Excel

### 断点续跑

大批量导出时，脚本会在输出目录下自动生成一个进度文件；也可以通过 `--progress-file` 显式指定路径。

进度文件会记录：

- 当前检索参数
- 当前输出目录
- 已完成批次数
- 已导出总量
- 恢复所需的结果页页码与页内偏移
- 已成功生成的批次 `enriched` 文件路径

恢复原则：

1. 重新执行完全相同的高级检索
2. 自动翻页到上次失败前的位置
3. 从记录的页内偏移继续勾选与导出
4. 已完成批次直接复用，不重复下载

首次运行示例：

```bash
python cnki-search/scripts/cli.py advanced-search --query "新青年" --include-no-fulltext --date-to 2025
```

如果中途中断或失败，直接继续：

```bash
python cnki-search/scripts/cli.py advanced-search --progress-file "E:/Desk/xinqingnian/outputs/cnki-search/新青年/progress-新青年-xxxx.json"
```

如果希望固定进度文件路径：

```bash
python cnki-search/scripts/cli.py advanced-search --query "新青年" --include-no-fulltext --date-to 2025 --progress-file "E:/Desk/xinqingnian/outputs/cnki-search/新青年/progress.json"
```

### 大批量导出的翻页稳定性

当导出条数较大、结果页页码较深时，知网页面的翻页响应可能明显变慢。

当前脚本的处理策略不是单纯放大一次点击超时，而是优先保证稳定性：

- 翻页点击与结果页变化等待分开处理
- 结果页 `下一页` 支持有限重试
- 最后一次重试会退回到页面脚本点击
- 当前页全选与逐条勾选复选框支持稳定勾选与 JS 兜底
- 结果页局部刷新时，允许短暂解析异常并继续等待

相关默认配置位于 `config.py`，并支持被 `.env` 中的同名环境变量覆盖：

- `page_timeout=30`：通用页面等待超时
- `navigation_timeout=30`：页面跳转等待超时
- `captcha_timeout=120`：验证码等待超时
- `action_timeout=10`：单次点击、滚动等页面动作超时
- `page_change_timeout=90`：翻页、排序、每页条数切换等结果页变化等待超时

如果站点当日响应特别慢，可直接按需调整这些配置；大批量导出的优化目标是“整批稳定完成”，不是“每一步都尽快返回”。

### 混合表头处理

CNKI 导出的单个元数据文件里，可能混合出现多种文献类型，每一段数据的表头字段和字段顺序都可能不同。

当前代码不会再假设整张表只有一套固定表头，而是按“分段表头”解析：

1. 逐行扫描原始导出内容
2. 遇到包含 `SrcDatabase-`、`Title-`、`Author-` 的行，视为新的表头段
3. 后续真实数据按这套表头映射
4. 遇到下一套表头时切换映射规则
5. 最终把所有字段做并集，合并成一个统一主表
6. 没有对应字段的数据留空
7. 保留原始数据顺序不变
8. 再按顺序把 TXT 中 `[1] [2] [3] ...` 依次填入 `参考格式`

这意味着最终产物只有一个主 `sheet`，但可以同时容纳期刊、报纸等不同类型记录。

### 产物说明

高级导出目录中通常会看到：

- 清理后的批次元数据文件：`*-metadata-cleaned.xlsx`
- 批次参考文献 TXT：`*-reference.txt`
- 回填参考格式后的批次文件：`*-enriched.xlsx`
- 最终合并文件：`*-merged.xlsx`
- 断点续跑进度文件：`progress-*.json`
- JSON 结果文件：`*.json`

原始下载的脏 `metadata.xls` 不作为最终保留产物。

命令返回结果通常包含：

- `result_type`
- `status`
- `query`
- `total`
- `selected`
- `exported`
- `planned_download`
- `batch_count`
- `exported_batches`
- `core_only`
- `date_from`
- `date_to`
- `date_range`
- `url`
- `file_path`
- `final_file_path`
- `intermediate_files`
- `progress_file`

其中：

- `status=no_results` 表示检索成功但无结果
- `final_file_path` 是最终合并文件路径
- `intermediate_files` 是批次中间产物路径列表
- `progress_file` 是断点续跑进度文件路径

## 输出行为

默认情况下，命令会：

- 在终端输出可读结果
- 把结果 JSON 保存到输出目录

如果不需要 JSON：

```bash
python cnki-search/scripts/cli.py search "人工智能" --no-save
```

如果只需要 JSON 输出：

```bash
python cnki-search/scripts/cli.py search "人工智能" --json-only
```

指定输出目录示例：

```bash
python cnki-search/scripts/cli.py advanced-search --query "人工智能" -o "F:/Github/academic-service/outputs/cnki-custom"
```

## 验证码与人工交互

CNKI 可能出现滑块验证码。当前策略不是自动绕过，而是人工处理：

- 检测到验证码后，等待你在浏览器中手动完成
- 你可以完成后回终端按回车，也可以等待脚本自动检测验证码消失

高级导出过程中如果弹出个人登录框：

- 脚本会尝试从 `.env` 中读取 `CNKI_USERNAME` / `CNKI_PASSWORD`
- 自动登录成功后重新进入导出流程
- 如果没有配置账号密码，流程会报错中断

## 推荐使用顺序

```bash
python cnki-search/scripts/cli.py login
python cnki-search/scripts/cli.py search "大模型 教育" -n 10 --json-only --no-save
python cnki-search/scripts/cli.py advanced-search --query "AI 知识服务" -n 100
```

## 已知限制

- 页面结构依赖当前 CNKI DOM，知网改版后选择器可能失效
- 高级检索条件当前是固定模板，不支持任意字段组合
- 导出流程依赖登录态、验证码状态和个人导出权限
- 批量导出是否成功也依赖当前账号在知网页面的权限表现
- `advanced-search` 中的 `-n` 表示导出上限，不是解析当前页条数

## 调试建议

排查问题时优先加：

```bash
--debug
```

示例：

```bash
python cnki-search/scripts/cli.py advanced-search --query "AI 知识服务" -n 100 --debug
```

如果怀疑登录态失效：

```bash
python cnki-search/scripts/cli.py login
```
