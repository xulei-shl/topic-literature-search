# 开发变更记录
- **日期**: 2026-05-04
- **对应设计文档**: [CNKI 分批导出与最终合并设计文档](/E:/Desk/xinqingnian/docs/design/cnki_batch_export_20260504.md)

## 1. 变更摘要
为 CNKI 高级检索新增固定双条件分批导出能力，支持截止日期、总下载上限、GB/T 引文 TXT 回填到 Excel 的 `参考格式` 列，并在本地合并所有批次为最终 `xlsx`。

## 2. 文件清单
- `cnki-search/scripts/cli.py`: 扩展高级检索参数和截止日期校验。
- `cnki-search/scripts/interactor.py`: 新增固定双条件配置、500 条分批勾选、双格式导出与批次游标推进。
- `cnki-search/scripts/export_processor.py`: 新增 TXT 解析、Excel 回填、批次合并能力。
- `cnki-search/scripts/exceptions.py`: 新增导出处理异常类型。
- `cnki-search/scripts/utils.py`: 扩展高级导出结果打印字段。
- `tests/test_cnki_export_processor.py`: 新增 5 个本地处理与参数校验测试。

## 3. 测试结果
- [x] 单元测试通过
- [x] 核心路径静态校验通过
