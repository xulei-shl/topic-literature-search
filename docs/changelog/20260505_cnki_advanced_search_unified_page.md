# 开发变更记录
- **日期**: 2026-05-05
- **对应设计文档**: [docs/design/cnki_advanced_search_unified_page_20260505.md](../design/cnki_advanced_search_unified_page_20260505.md)

## 1. 变更摘要
- 高级检索统一在首页点击“高级检索”后切换到 `kns8s` 新页面执行表单填写。
- `--date-from` 与 `--date-to` 改为仅接受年份输入。
- 新增 `--include-no-fulltext` 参数，用于取消默认勾选的“仅看有全文”。
- `--core` 场景下调整来源类别勾选逻辑，取消“全部期刊”，并勾选 `WJCI / SCI / EI / 北大核心 / CSSCI / CSCD / AMI`。

## 2. 文件清单
- `cnki-search/scripts/cli.py`: 收紧高级检索日期参数格式，新增 `--include-no-fulltext`
- `cnki-search/scripts/interactor.py`: 统一新页入口、调整年份输入、补充“仅看有全文”和核心来源勾选逻辑
- `tests/test_cnki_export_processor.py`: 更新 CLI 参数校验测试
- `tests/test_cnki_interactor.py`: 更新高级检索表单交互测试

## 3. 测试结果
- [x] 单元测试通过
- [ ] 核心路径验证通过
