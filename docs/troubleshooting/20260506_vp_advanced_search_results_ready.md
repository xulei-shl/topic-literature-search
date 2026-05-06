# 故障复盘报告
- **日期**: 2026-05-06
- **严重级别**: Medium

## 1. 问题现象
在维普高级检索页面输入检索词并点击`检索`后，程序未继续执行后续流程，表现为不会自动清空已选、勾选结果、翻页导出元数据与参考文献。

## 2. 根本原因 (Root Cause)
结果页提交后的等待逻辑过于依赖旧版 DOM 组合。因为 [vp-search/scripts/interactor.py](/E:/Desk/xinqingnian/vp-search/scripts/interactor.py) 中 `_wait_for_results_ready()` 只将 `.search-result` 与分页或复选框同时出现视为“结果页已就绪”，所以当结果页先渲染 `#headerpager`、`span.selected-count`、`#selectPageSize` 等节点，或者页面结构调整后 `.search-result` 未及时满足条件时，流程会一直等待到超时，后续批量导出链路不会继续执行。

## 3. 解决方案
增强结果页识别逻辑，改为同时兼容 `#headerpager`、`#hidShowTotalCount`、`span.selected-count`、`#selectPageSize`、`input[name='selectArticleAll']` 等结果页标志；在检索结果加载后，优先尝试切换到“每页显示 50”，并等待分页状态或当前页条数变化后再继续后续批量导出流程。

## 4. 预防措施
- 为结果页就绪判断新增回归测试，覆盖仅出现 `span.selected-count` 时也应视为结果页已就绪的场景。
- 为“每页显示 50”优化新增回归测试，确保页面可选时会执行切换逻辑。
