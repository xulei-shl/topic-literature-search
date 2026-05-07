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
