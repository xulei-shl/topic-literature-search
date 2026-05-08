# 故障复盘报告
- **日期**: 2026-05-08
- **严重级别**: Medium

## 1. 问题现象
万方高级检索使用 `--progress-file` 续跑时，进度文件中的 `current_page` 可能被写成“本批次失败前浏览到的现场页”，而不是“下次恢复应从哪一页开始”。例如前 2 个批次共成功导出 1000 条、理论上应从第 21 页继续，但中途翻页到第 30 页后导出失败，进度文件却写成了 `current_page=30`，导致续跑可能跳过未成功导出的数据。

## 2. 根本原因 (Root Cause)
共享导出骨架在批次失败或中断时，直接按当前页面上下文写入进度快照；而万方导出前又会提前缓存结果页上下文。因为批次内勾选会继续翻页，所以失败时读取到的是“当前浏览页”，不是“最后一次成功提交后的恢复游标”。所以 `exported_total/exported_batches` 与 `current_page/current_row_offset` 表示的语义不一致，续跑时就会出现遗漏或重复风险。

## 3. 解决方案
将 `current_page/current_row_offset` 明确定义为“已成功提交后的下一恢复游标”，并在共享骨架中单独维护：

- 只有当一个批次的导出、清洗、回填、报告都成功后，才推进恢复游标。
- 批次失败或中断时，进度文件继续写入上一次成功提交后的游标，不再写入失败现场页。
- 站点侧的 `_prepare_next_batch_cursor` 改为同时返回 `current_page` 和 `current_row_offset`，保证整页完成后能把恢复点推进到下一页起点。
- 进度快照中的 `page_text` 基于恢复游标重建，避免出现 `current_page=21` 但 `page_text=30/153` 这类不一致状态。

## 4. 预防措施
- 新增共享骨架测试，覆盖“批次失败时保留最后一次成功游标”场景。
- 新增万方测试，覆盖“进度快照使用恢复游标页码”和“整页完成后恢复点推进到下一页”两个场景。
- 回归执行：
  - `python -m pytest tests/test_advanced_export_flow.py tests/test_wanfang_interactor.py tests/test_cnki_interactor.py tests/test_vp_interactor.py`
  - `python -m compileall src/core wanfang-search/scripts cnki-search/scripts vp-search/scripts`
