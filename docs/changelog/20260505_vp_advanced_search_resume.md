# 开发变更记录
- **日期**: 2026-05-05
- **对应设计文档**: [docs/design/cnki_advanced_search_resume_20260505.md](E:/Desk/xinqingnian/docs/design/cnki_advanced_search_resume_20260505.md)

## 1. 变更摘要
- 为 `vp-search advanced-search` 增加断点续跑进度文件能力，支持首次运行自动生成进度文件。
- 支持仅通过 `--progress-file` 恢复历史检索参数，并校验 CLI 参数与历史参数一致性。
- 为维普批量导出流程增加运行态快照、异常留痕、历史批次文件复用与最终合并恢复。

## 2. 文件清单
- `vp-search/scripts/cli.py`: 修改，增加 `--progress-file` 参数并补充结果保存逻辑。
- `vp-search/scripts/interactor.py`: 修改，接入恢复、进度持久化、失败留痕和批次复用。
- `vp-search/scripts/progress_store.py`: 新增，负责进度文件读写与参数解析。
- `vp-search/scripts/utils.py`: 修改，补充人类可读输出中的进度文件与恢复标记。
- `tests/test_vp_interactor.py`: 修改，补充恢复页码、历史批次校验、进度快照测试。
- `tests/test_vp_progress_store.py`: 新增，补充进度文件读写与参数恢复测试。

## 3. 测试结果
- [x] 单元测试通过
- [x] 核心路径验证通过

执行命令：
- `python -m pytest tests/test_vp_progress_store.py tests/test_vp_interactor.py`
- `python -m pytest tests/test_vp_export_processor.py tests/test_vp_interactor.py tests/test_vp_progress_store.py`
