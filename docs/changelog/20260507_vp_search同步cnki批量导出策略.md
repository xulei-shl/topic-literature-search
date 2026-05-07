# 开发变更记录
- **日期**: 2026-05-07
- **对应设计文档**: [VP 检索批量导出同步 CNKI 策略设计文档](../design/vp_sync_cnki_batch_export_strategy_20260507.md)

## 1. 变更摘要
- 同步 `vp-search` 与 `cnki-search` 的高级检索批量导出勾选分流策略。
- 全量导出模式改为按页窗口推进，不再为了补足批次目标跨页追数。
- `-n` 限定数量模式保持严格补足目标数的行为。
- 为维普批次勾选结果补充 `start_page` / `end_page`，让共享骨架生成的批次报告带上页码范围。
- 在 `vp-search` CLI 结果与可读输出中补充总报告与批次报告路径透出。

## 2. 文件清单
- `vp-search/scripts/vp_selection_ops.py`: 修改，接入 `strict_target` 分流逻辑并回传批次页码范围。
- `vp-search/scripts/cli.py`: 修改，在 `saved_files` 中追加 `report` 与 `batch_reports`。
- `vp-search/scripts/utils.py`: 修改，在 CLI 可读输出中展示总报告路径。
- `tests/test_vp_interactor.py`: 修改，补充维普两种勾选策略、页码范围与报告路径透出测试。

## 3. 测试结果
- [x] `pytest tests/test_vp_interactor.py -q`
- [x] `pytest tests/test_advanced_export_flow.py tests/test_vp_interactor.py -q`
- [x] `python -m py_compile vp-search/scripts/vp_selection_ops.py vp-search/scripts/cli.py vp-search/scripts/utils.py tests/test_vp_interactor.py`
