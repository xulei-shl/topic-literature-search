# 开发变更记录
- **日期**: 2026-05-06
- **对应设计文档**: `docs/design/interactor_shared_advanced_export_refactor_20260506.md`

## 1. 变更摘要
完成高级检索交互器重构：抽离共享批量导出骨架，新增页面原子操作工具，并继续将 CNKI/维普站点实现按职责拆分到多个 `*_ops.py` 模块；删除无实际职责的 `interactor.py` 门面文件。

## 2. 文件清单
- `src/core/advanced_export_flow.py`: 新增共享主流程骨架。
- `src/core/advanced_export_types.py`: 新增共享类型定义。
- `src/utils/playwright_page.py`: 新增页面原子操作工具。
- `src/utils/result_output.py`: 扩展导出文件名与路径构造函数。
- `cnki-search/scripts/cnki_search_interactor.py`: 新增 CNKI 主交互实现。
- `cnki-search/scripts/cnki_*_ops.py`: 按职责拆分 CNKI 站点逻辑。
- `vp-search/scripts/vp_search_interactor.py`: 新增维普主交互实现。
- `vp-search/scripts/vp_*_ops.py`: 按职责拆分维普站点逻辑。
- `cnki-search/scripts/interactor.py`: 删除门面文件。
- `vp-search/scripts/interactor.py`: 删除门面文件。
- `tests/test_advanced_export_flow.py`: 新增共享骨架单测。
- `tests/test_*`: 调整脚本模块加载方式，避免跨目录同名模块污染。

## 3. 测试结果
- [x] `pytest`
- [x] 共享骨架单测通过
- [x] CNKI / 维普交互回归测试通过
