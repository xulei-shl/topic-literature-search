# 开发变更记录
- **日期**: 2026-05-06
- **对应设计文档**: 会话内已批准的“续跑恢复优先使用可见数字页跳转”方案

## 1. 变更摘要

- 优化 CNKI 高级检索 `--progress-file` 续跑恢复逻辑。
- 恢复页码时，优先解析当前分页条中可见的数字页按钮。
- 若目标页当前可见，则直接点击目标页；若不可见，则点击当前可见范围内不超过目标页的最大数字页。
- 当数字页跳转不可用时，继续复用原有“下一页”逻辑作为兜底，避免影响现有稳定性。

## 2. 文件清单

- `cnki-search/scripts/interactor.py`: 修改，新增恢复跳页候选解析与数字页点击逻辑
- `tests/test_cnki_interactor.py`: 修改，补充恢复跳页优先级与候选页选择测试

## 3. 测试结果

- [x] 单元测试通过
- [ ] 核心路径验证通过

已执行：

- `python -m pytest tests/test_cnki_interactor.py tests/test_cnki_progress_store.py`
- `python -m compileall cnki-search/scripts`
