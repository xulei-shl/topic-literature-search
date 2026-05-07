# 开发变更记录
- **日期**: 2026-05-06
- **对应设计文档**: 无

## 1. 变更摘要
调整维普高级检索导出收尾逻辑，使其与 CNKI 保持一致：
- 当导出页复用当前结果页且当前批次已经是最后一批时，不再强制返回检索结果页并等待加载。
- 在进入导出流程前缓存最近一次可用的结果页上下文，确保最终进度快照仍能保留正确的页码信息。

## 2. 文件清单
- `src/core/advanced_export_flow.py`: 修改批次导出前的结果页恢复标记传递逻辑。
- `src/core/advanced_export_types.py`: 修改批次勾选结果结构，补充结果页恢复标记。
- `vp-search/scripts/vp_export_ops.py`: 修改同页导出收尾逻辑，支持按批次跳过回结果页等待。
- `vp-search/scripts/vp_progress_ops.py`: 修改进度页上下文提取逻辑，增加缓存与回退读取。
- `vp-search/scripts/vp_search_interactor.py`: 修改维普批次导出调用参数，导出前缓存结果页上下文。
- `tests/test_vp_interactor.py`: 新增 3 条测试，覆盖跳过回页、导出参数传递、进度上下文缓存回退。

## 3. 测试结果
- [x] 单元测试通过
- [x] 核心路径验证通过
