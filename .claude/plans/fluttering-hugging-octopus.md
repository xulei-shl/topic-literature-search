# VP 批量勾选-翻页-导出性能优化

## Context

VP 的批量勾选循环（逐页勾选 → 满 500 条导出）比 CNKI 慢很多。根因有 4 个：

1. **全选脆弱**：`_try_select_all_on_current_page` 的 4 个 XPath selector 找不到 Layui wrapper 时静默回退逐条勾选（50次/页）
2. **逐条勾选 4 策略**：`_ensure_checkbox_checked` 尝试 Layui JS → Layui click → native check → JS 兜底，每步 `scroll_into_view_if_needed(3000ms)`
3. **超时上限截断**：`_action_timeout_ms` capped at 3s, `_page_change_timeout_seconds` capped at 5s，导致翻页频繁超时重试（每页额外 +3s）
4. **验证过重**：`_is_checkbox_checked` 做 native + XPath 双重检查，每页验证 3 轮

## Changes

### 1. [vp_page_ops.py](vp-search/scripts/vp_page_ops.py) — 超时改为下限保护

`_action_timeout_ms`: `min(int(timeout * 1000), 3000)` → `max(int(timeout * 1000), 1000)`
`_page_change_timeout_seconds`: `min(float(timeout), 5.0)` → `max(float(timeout), 1.0)`

对齐 CNKI 策略，避免因上限截断导致的虚假超时和重试级联。

### 2. [vp_checkbox_list_ops.py](vp-search/scripts/vp_checkbox_list_ops.py) — 简化验证和 wrapper 解析

- `_is_checkbox_checked`: 删掉 XPath wrapper class 检查，仅保留 `checkbox.is_checked()`（与 CNKI 一致）
- `_resolve_checkbox_click_target`: 精简为 2 个 XPath（`../div[...]` 和 `following-sibling::div[...]`），删除 3 个备选 XPath
- 删除 `_filter_result_row_checkbox_items` 中的 `get_attribute` 双属性查询，改为只检查 `name` 属性

### 3. [vp_selection_ops.py](vp-search/scripts/vp_selection_ops.py) — 重写全选和逐条勾选

**`_try_select_all_on_current_page` 重写：**
- 直接定位 `span.select-all div.layui-form-checkbox`（基于用户提供的实际 HTML）
- 已选中（`layui-form-checked` class）→ 直接返回 True
- 未选中 → `click(force=True)` → JS fallback
- 删掉 DEBUG 模式校验分支、删掉 `_extract_selected_count` 调用

**`_ensure_checkbox_checked` 精简为 2 策略：**
- 策略 1：`scroll_into_view_if_needed` + `check(force=True)`（对齐 CNKI）
- 策略 2：JS 兜底（set checked + dispatch events）
- 删除策略 1/2 的 Layui wrapper JS/原生点击（4 策略 → 2 策略）
- 删除 `click_target` 参数依赖

**`_select_rows_on_current_page` 优化：**
- 删除内部二次调用 `_current_page_checkbox_items()`，复用已有的 checkbox_items

### 4. [vp_navigation_ops.py](vp-search/scripts/vp_navigation_ops.py) — 删除翻页 expect_navigation 快速路径

`_goto_next_results_page` 中的策略 A（`expect_navigation`）在 VP 的 AJAX 翻页场景下不会触发（URL 不变），反而浪费 5 秒等待。直接走策略 B（`_wait_for_results_changed`）。

## Verification

1. 运行现有测试确认不回归：`pytest tests/test_vp_interactor.py -v`
2. 检查 `_action_timeout_ms` / `_page_change_timeout_seconds` 在 VP 各模块的消费方无编译错误
3. 实际运行一次 VP 检索验证勾选-翻页-导出流程正常
