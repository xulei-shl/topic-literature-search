# topic-literature-search

特定主题下的中文学术文献全面检索工具。

## 功能

- 检索三大中文数据库（CNKI、维普、万方）
- 合并去重
- 大模型相关性评分过滤

## 模块

- `cnki-search/` - CNKI 检索脚本（已完成）
- `vp-search/` - 维普检索（开发中）
- `wanfang-search/` - 万方检索（待开发）
- `llm-filter/` - 大模型评分过滤（待开发）

## 依赖

- Python 3.10+
- Camoufox + Playwright
- LLM API（如 OpenAI、通义千问等）