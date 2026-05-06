# 开发变更记录
- **日期**: 2026-05-05
- **对应设计文档**: [维普批量导出实现计划](/E:/Desk/xinqingnian/docs/维普.md)

## 1. 变更摘要
- 新增 `vp-search` 检索脚本骨架，支持手动登录与高级检索批量导出。
- 实现维普高级检索参数 `--query / --date-from / --date-to / --core / --max-download`。
- 实现 500 条分批勾选、Excel 与参考文献 TXT 双格式导出、本地回填 `参考格式` 列以及最终合并产物。
- 新增 `vp-search` 本地处理与页面交互测试，覆盖参数校验、导出清洗、核心期刊勾选与翻页重试逻辑。

## 2. 文件清单
- `vp-search/scripts/cli.py`: 新增命令行入口与参数校验。
- `vp-search/scripts/config.py`: 新增维普站点配置与输出目录配置。
- `vp-search/scripts/browser.py`: 新增浏览器与会话管理。
- `vp-search/scripts/interactor.py`: 新增维普高级检索、分批勾选、翻页与导出主流程。
- `vp-search/scripts/result_parser.py`: 新增结果页概要解析。
- `vp-search/scripts/export_processor.py`: 新增导出表格清洗、TXT 回填与批次合并。
- `vp-search/scripts/utils.py`: 新增输出与 JSON 保存工具。
- `vp-search/scripts/exceptions.py`: 新增维普脚本异常定义。
- `vp-search/scripts/__init__.py`: 新增包标记文件。
- `vp-search/scripts/__main__.py`: 新增模块运行入口。
- `tests/test_vp_export_processor.py`: 新增导出处理与 CLI 参数测试。
- `tests/test_vp_interactor.py`: 新增交互流程测试。

## 3. 测试结果
- [x] `python -m unittest tests.test_vp_export_processor tests.test_vp_interactor`
- [x] `python -m compileall vp-search\scripts tests\test_vp_export_processor.py tests\test_vp_interactor.py`
- [ ] 真实站点端到端导出验证
