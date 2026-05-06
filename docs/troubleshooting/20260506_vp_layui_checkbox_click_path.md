# 故障复盘报告
- **日期**: 2026-05-06
- **严重级别**: Medium

## 1. 问题现象
维普高级检索结果页中，程序可以执行清空已选和切换“每页显示 50”，但在勾选全选或结果项复选框时没有实际选中文献，导致后续无法继续批量导出。

## 2. 根本原因 (Root Cause)
因为结果页使用 Layui 自定义复选框，真实可点击节点是 `div.layui-form-checkbox`，所以仅对隐藏的 `input[name='selectArticleAll']` 或 `input[name='selectArticle']` 调用 `check()` 不能稳定触发页面选中状态。原有实现把原生 `input` 当作主点击目标，因此页面上的“已选 N 条”没有正常增长。

## 3. 解决方案
在 [vp-search/scripts/interactor.py](/E:/Desk/xinqingnian/vp-search/scripts/interactor.py) 中新增复选框点击目标解析逻辑，优先定位并点击 `input` 相邻或外层关联的 `div.layui-form-checkbox`；只有外壳不可用时，才回退到原生 `input.check()` 和 JS 兜底。勾选完成后的校验同时兼容原生 `input` 状态与 Layui 选中类名。

## 4. 预防措施
- 新增回归测试，覆盖“必须点击 Layui 可视化外壳”这一场景。
- 后续若页面继续调整复选框结构，优先扩展点击目标解析逻辑，而不是直接堆叠更多 `input` 兜底代码。
