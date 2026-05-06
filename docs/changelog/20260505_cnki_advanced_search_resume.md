# 开发变更记录
- **日期**: 2026-05-05
- **对应设计文档**: `docs/design/cnki_advanced_search_resume_20260505.md`

## 1. 变更摘要

为 CNKI `advanced-search` 增加断点续跑能力：

- 新增进度文件自动生成与显式指定能力
- 支持通过进度文件恢复历史检索参数
- 支持按页码与页内偏移恢复批量导出位置
- 复用已完成批次的 `enriched` 文件，避免重复导出
- 在失败与中断场景写入现场信息，便于后续继续执行和排障
- 修复无进度文件启动新任务时，`store_true` 布尔参数被误判为进度冲突的问题

## 2. 文件清单

- `cnki-search/scripts/cli.py`: 修改，新增 `--progress-file` 并接入进度文件输出
- `cnki-search/scripts/interactor.py`: 修改，接入断点保存、恢复跳转、失败留痕与恢复合并
- `cnki-search/scripts/progress_store.py`: 新增，负责进度文件读写、默认路径生成、参数恢复校验
- `cnki-search/scripts/utils.py`: 修改，补充进度文件与恢复模式输出
- `tests/test_cnki_interactor.py`: 修改，新增恢复页码测试
- `tests/test_cnki_progress_store.py`: 新增 4 个进度文件测试
  - 后续补充 1 个无进度文件布尔参数回归测试

## 3. 测试结果

- [x] 单元测试通过
- [x] 核心路径验证通过

已执行：

- `python -m pytest tests/test_cnki_export_processor.py tests/test_cnki_interactor.py tests/test_cnki_progress_store.py tests/test_search_timeout_config.py`
- `python -m compileall cnki-search/scripts`
