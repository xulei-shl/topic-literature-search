# 开发变更记录
- **日期**: 2026-05-05
- **对应设计文档**: [docs/design/cnki_advanced_search_resume_20260505.md](E:/Desk/xinqingnian/docs/design/cnki_advanced_search_resume_20260505.md)

## 1. 变更摘要
- 修复 `vp-search advanced-search` 在未传 `--progress-file` 时，`--core` 被错误判定为“与进度文件冲突”的问题。
- 调整布尔参数合并逻辑：只有真实存在历史进度参数时，才执行布尔冲突校验。
- 补充无进度文件场景下 `store_true` 参数的回归测试。

## 2. 文件清单
- `vp-search/scripts/progress_store.py`: 修改，修复布尔参数合并逻辑。
- `tests/test_vp_progress_store.py`: 修改，新增无进度文件时 `core_only=True` 的回归测试。

## 3. 测试结果
- [x] 单元测试通过
- [x] 核心路径验证通过

执行命令：
- `python -m pytest tests/test_vp_progress_store.py tests/test_vp_interactor.py tests/test_vp_export_processor.py`
