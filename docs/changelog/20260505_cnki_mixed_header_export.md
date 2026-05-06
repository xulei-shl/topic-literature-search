# 开发变更记录
- **日期**: 2026-05-05
- **对应设计文档**: [需求.md](/E:/Desk/xinqingnian/docs/需求.md)

## 1. 变更摘要
重构 CNKI 批次导出表格清理逻辑，支持同一导出文件中混合出现多种文献类型表头。新逻辑按分段表头扫描原始行数据，将不同表头下的数据统一映射到单一主表中，并保持原始出现顺序不变，以便与 TXT 参考文献按序号稳定对应。

## 2. 文件清单
- `cnki-search/scripts/export_processor.py`: 重写导出表格解析与清理逻辑
- `tests/test_cnki_export_processor.py`: 修正脚本导入路径并新增混合表头测试
- `docs/changelog/20260505_cnki_mixed_header_export.md`: 新增变更记录

## 3. 测试结果
- [x] 单元测试通过
- [ ] 核心路径验证通过

补充说明：已执行 `python -m unittest tests.test_cnki_export_processor`，覆盖普通单表头和混合表头两类导出结构。
