# topic-literature-search

特定主题下的中文学术文献全面检索工具。

## 功能

- 检索三大中文数据库（CNKI、维普、万方）
- 顶层统一入口并发调度高级检索
- 合并去重
- 大模型相关性评分过滤

## 模块

- `cnki-search/` - CNKI 检索脚本（已完成）
- `vp-search/` - 维普检索脚本（已完成高级检索与导出）
- `wanfang-search/` - 万方检索脚本（已完成高级检索与导出）
- `llm-filter/` - 大模型评分过滤（待开发）
- `cli.py` - 顶层统一调度入口，仅负责并发调用各库 `advanced-search`

## 依赖

- Python 3.10+
- Camoufox + Playwright
- LLM API（如 OpenAI、通义千问等）

## 用法

### 顶层统一入口

顶层 [cli.py](/F:/Github/topic-literature-search/cli.py) 目前只支持 `advanced-search`，负责将参数透传给各子模块，并按目标并发执行。

示例：

```bash
python cli.py advanced-search --all --query 新青年 --date-from 1915 --date-to 1925 -n 100
python cli.py advanced-search --cnki --query 新青年 -n 100
python cli.py advanced-search --vp --wanfang --query 新青年 --date-from 1915
```

目标参数：

- `--all`：并发执行 `cnki`、`vp`、`wanfang`
- `--cnki`：只执行 CNKI
- `--vp`：只执行维普
- `--wanfang`：只执行万方

说明：

- 顶层入口只做调度，不包含各模块的具体检索逻辑
- `--all` 与 `--cnki/--vp/--wanfang` 不能同时使用
- 任一子任务失败不会中断其他并发任务
- 最终退出码规则为：全部成功返回 `0`，存在失败返回 `1`

### 子模块入口

各数据库原有脚本入口仍可单独使用：

```bash
python cnki-search/scripts/cli.py advanced-search --query 新青年 -n 100
python vp-search/scripts/cli.py advanced-search --query 新青年 -n 100
python wanfang-search/scripts/cli.py advanced-search --query 新青年 -n 100
```

### 合并 Excel

合并多个数据源的 Excel 文件，并进行预处理：

```bash
python cli.py merge-excel --query 新青年 --all
python cli.py merge-excel --query 新青年 --cnki --wanfang
```

参数说明：

- `--query`：检索关键词，用于定位 `outputs` 下的目录
- `--all`：合并全部数据源（cnki、vp、wanfang）
- `--cnki/--vp/--wanfang`：指定要合并的数据源，可组合使用

预处理操作：

1. **繁体转简体**：将所有文本中的繁体中文转换为简体中文
2. **清理参考格式**：去掉参考格式开头的序号（如 `[1]`、`[123]`）
3. **新增列**：
   - `检索数据库`：标注数据来自哪个数据库（cnki/vp/wanfang）
   - `题名含《新青年》`：检查题名是否包含"《新青年》"
   - `摘要含《新青年》`：检查摘要是否包含"《新青年》"
   - `关键词含《新青年》`：检查关键词是否包含"《新青年》"

输出文件保存在 `outputs/` 目录，命名为 `{timestamp}-{query}-combined.xlsx`。

### 第二步：Excel 去重

对第一步合并生成的 Excel 文件进行去重，基于题名去除跨库重复记录，每条跨库重复记录保留非空列数最多的版本。

**命令格式：**

```bash
# 自动查找最新的合并文件并去重
python cli.py clean-excel --query 新青年

# 指定输入文件（跳过自动查找）
python cli.py clean-excel --query 新青年 --input outputs/20260101-120000-新青年-combined.xlsx
```

**参数说明：**

| 参数      | 必填 | 说明                                                         |
| --------- | ---- | ------------------------------------------------------------ |
| `--query` | 是   | 检索关键词，用于自动定位合并文件（与第一步的 `--query` 相同） |
| `--input` | 否   | 直接指定输入的 Excel 文件路径。若不提供，程序会在 `outputs/` 目录下自动查找最新生成的 `*-combined.xlsx` 文件 |

**去重规则：**

- 以 **题名** 列为判断依据，经标准化（全角标点→半角、去空格、转小写）后匹配
- **同来源库**的相同题名 → 全部保留（同一数据库内不进行去重）
- **跨来源库**的相同题名 → 视为重复，仅保留非空列数最多的那一行，其余淘汰
- 空题名行、来源库含"外文"的行被自动过滤

**预处理过滤顺序：**

1. 全空行 → 直接丢弃
2. 来源库含"外文" → 落入过滤文件
3. 题名为空 → 落入过滤文件
4. 正常行 → 参与去重判断

**输出文件：**

程序生成两个 Excel 文件和一个报告：

| 文件 | 后缀 | 说明 |
|------|------|------|
| 结果文件 | `*_deduplicated.xlsx` | 去重保留后的数据 |
| 过滤文件 | `*_filtered.xlsx` | 含"外文"、"空题名"、"重复"三个 sheet |
| 报告文件 | `*_deduplicated.md` | 去重统计概览 |

两个 Excel 的数据量之和等于原始数据量（不变式保证）。

**示例工作流：**

```bash
# 第一步：合并 CNKI、维普、万方的数据
python cli.py merge-excel --query 新青年 --all

# 第二步：对合并结果去重
python cli.py clean-excel --query 新青年
```

**参数说明：**

| 参数      | 必填 | 说明                                                         |
| --------- | ---- | ------------------------------------------------------------ |
| `--query` | 是   | 检索关键词，用于自动定位合并文件（与第一步的 `--query` 相同） |
| `--input` | 否   | 直接指定输入的 Excel 文件路径。若不提供，程序会在 `outputs/` 目录下自动查找最新生成的 `*-combined.xlsx` 文件 |

**去重规则：**

- 以 **题名** 列为唯一判断依据
- 相同题名的多条记录只保留第一条（按原文件中的出现顺序）
- 同时处理跨数据库重复（如同一篇文章在 CNKI 和万方中均出现）和同数据库内重复
- 自动保留所有其他列（包括第一步新增的“检索数据库”、“题名含有《新青年》”等）

**输出文件：**

去重后的文件保存在与输入文件相同的目录下，文件名在原文件名后添加 `_deduplicated` 后缀。
例如：`20260101-120000-新青年-combined_deduplicated.xlsx`

**示例工作流：**

```bash
# 第一步：合并 CNKI、维普、万方的数据
python cli.py merge-excel --query 新青年 --all

# 第二步：对合并结果去重
python cli.py clean-excel --query 新青年
```

### 第三步：LLM 主题相关性评估

对去重后的 Excel 进行大模型主题相关性评估，筛选出与历史上的《新青年》杂志相关的文献。

**命令格式：**

```bash
# 自动查找最新的去重文件并评估
python cli.py llm-filter --query 新青年

# 指定输入文件（跳过自动查找）
python cli.py llm-filter --query 新青年 --input "F:/Github/topic-literature-search/outputs/20260101-120000-新青年_deduplicated.xlsx"

# 指定批次大小（覆盖 .env 配置）
python cli.py llm-filter --query 新青年 --batch-size 10
```

**参数说明：**

| 参数 | 必填 | 说明 |
|------|------|------|
| `--query` | 是 | 检索关键词，用于自动定位去重文件（未指定 --input 时生效） |
| `--input` | 否 | 直接指定输入的 Excel 文件路径（支持任意 xlsx 文件） |
| `--batch-size` | 否 | 批次大小，默认从 `.env` 读取（`LLM_BATCH_SIZE`） |

**说明：**

- 使用 `--input` 时，程序会直接使用指定的文件，不再自动查找去重文件
- 输出文件保存在与输入文件相同的目录下，文件名在原文件名后添加 `_llm_filtered` 后缀

**配置说明（.env）：**

```env
# LLM 配置：key|base_url|model，多组用逗号分隔
LLM_CONFIG=sk-xxx|https://api.edgefn.net|deepseek-v4-pro,sk-yyy|https://api.edgegpt.net|gpt-4

# 批次大小（默认 5）
LLM_BATCH_SIZE=5

# 最大重试轮次（默认 3）
LLM_MAX_ROUNDS=3
```

**处理逻辑：**

1. 自动查找最新的 `*_deduplicated.xlsx` 文件
2. 并发调用 LLM 评估每条文献的主题相关性
3. 支持多组配置（key + base_url + model）轮询使用
4. 每组配置重试 3 次，间隔递增（3s → 6s → 10s）
5. 最多 3 轮重试：第 1 轮处理所有行，错误行在第 2、3 轮重新处理

**新增 Excel 列：**

| 列名 | 说明 |
|------|------|
| `is_target_magazine` | 是否是关于历史上的《新青年》杂志（true/false） |
| `relevance_score` | 相关度评分（0-10 分） |
| `relevance_level` | 相关级别（High/Medium/Low/Irrelevant） |
| `reasoning` | 打分理由（限 50 字） |
| `historical_keywords` | 历史关键词（JSON 数组） |
| `LLM返回JSON` | LLM 原始返回 JSON |
| `LLM错误信息` | 错误信息（成功时为空） |

**输出文件：**

评估后的文件保存在与输入文件相同的目录下，文件名在原文件名后添加 `_llm_filtered` 后缀。
例如：`20260101-120000-新青年_deduplicated_llm_filtered.xlsx`

## 数据导出可靠性机制

三个数据库模块（CNKI / 维普 / 万方）共享同一套导出容错与中断续跑机制。

### 第一层：批次级即时校验 + 重试（`BaseAdvancedExportFlow`）

每个批次导出并完成数据合并（enrich）后，自动校验该批次的 enriched 文件行数：

```
实际行数 < 预期行数 - 5  →  最多重试 3 次
3 次仍不足               →  接受当前结果，继续后续批次
```

预期行数 = `min(EXPORT_BATCH_SIZE, 剩余条数)`。VP 每批次 100 条，CNKI/万方每批次 500 条。
重试时自动清理该批次临时文件，重新执行勾选 → 导出 → enrich 全流程，不需要用户干预。

### 第二层：年份级兜底校验 + 单轮重跑（`*YearlyExportMixin`）

逐年导出模式下，每个年份的全部批次完成后，对比维普页面报告总数与 merged.xlsx 实际行数：

```
差值 > 动态容差阈值  →  清空该年目录 → 从零导出（仅 1 轮）
重跑后仍不一致       →  接受现有结果，merge 全部数据，进入下一年的处理
```

动态容差阈值公式：`max(10, ceil(reported_total / EXPORT_BATCH_SIZE) × 5)`

| 模块 | 每批条数 | 1000 条时容差 | 5000 条时容差 |
|------|----------|---------------|---------------|
| VP   | 100      | 50            | 250           |
| CNKI | 500      | 10            | 50            |
| 万方 | 500      | 10            | 50            |

### 中断续跑

- **年份循环内的中断**：通过年份子目录下的 `progress-*.json` 自动恢复，续跑从上次中断的批次继续
- **重跑阶段的中断**：通过外层进度文件中的 `validation_rerun_pending_years` 列表恢复，重启后仅重跑未完成的年份，不重新校验全部年份
