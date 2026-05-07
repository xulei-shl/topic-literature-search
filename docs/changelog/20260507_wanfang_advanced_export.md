# 开发变更记录
- **日期**: 2026-05-07
- **对应设计文档**: [wanfang-search-design.md](../plan/wanfang-search-design.md)

## 1. 变更摘要
- 完成 `wanfang-search` 的高级检索批量导出主链路，补齐导出、进度恢复、主编排器与 CLI。
- 补充万方导出文件本地处理，支持清理 XLS 类型标签首行、回填参考格式列、合并批次结果。
- 增加万方单元测试，覆盖进度文件、导出后处理、主流程关键契约与 CLI 输出。
- 修复高级检索页条件行定位，改为基于真实输入框与行内下拉结构识别，避免因容器类名不稳定导致“条件行不足”。
- 调整高级检索填表策略，保持页面默认检索字段，仅修改第二行逻辑为 `或`，避免无意义下拉切换干扰后续流程。

## 2. 文件清单
- `wanfang-search/scripts/progress_store.py`: 新增进度文件存储。
- `wanfang-search/scripts/wanfang_form_ops.py`: 修复高级检索条件行与字段下拉定位逻辑。
- `wanfang-search/scripts/wanfang_form_ops.py`: 调整高级检索主流程，默认跳过检索字段下拉切换。
- `wanfang-search/scripts/wanfang_progress_ops.py`: 新增断点续跑与进度快照逻辑。
- `wanfang-search/scripts/wanfang_export_ops.py`: 新增批量引用导出流程。
- `wanfang-search/scripts/export_processor.py`: 新增 XLS/TXT 后处理逻辑。
- `wanfang-search/scripts/wanfang_search_interactor.py`: 新增主编排器并接入共享骨架。
- `wanfang-search/scripts/cli.py`: 新增 CLI 命令入口。
- `wanfang-search/scripts/wanfang_navigation_ops.py`: 修正分页导航实现并补齐重试/恢复逻辑。
- `tests/test_wanfang_progress_store.py`: 新增进度文件测试。
- `tests/test_wanfang_export_processor.py`: 新增导出后处理与 CLI 参数测试。
- `tests/test_wanfang_interactor.py`: 新增主流程契约、表单定位、进度、CLI 输出测试。

## 3. 测试结果
- [x] 单元测试通过
- [x] 核心路径验证通过
- 运行命令: `python -m pytest tests -k wanfang -v`
- 运行命令: `python -c "import compileall, pathlib; import sys; ok = compileall.compile_dir(str(pathlib.Path('wanfang-search/scripts')), quiet=1); sys.exit(0 if ok else 1)"`
