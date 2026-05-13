# VP 搜索性能优化计划

## Context
VP 搜索模块在高级检索后的批量勾选-翻页-导出流程中，步骤间衔接过慢，存在大量不必要的等待时间。用户反馈 4 个具体问题，并要求参考 CNKI 模块的优化模式来重构 VP。

## 问题与方案

### 问题 1: 清空已选弹出框有时无法定位

**根因**: `_dismiss_confirm_dialog_if_present` 逐个 selector 尝试，每个 `wait_for(state="visible", timeout=3000)` 等待 3 秒才超时切换下一个，导致定位慢且不稳定。

**修复**: 参考 CNKI 的简洁处理方式，先定位对话框容器再取其内部按钮，减少轮询次数。

**文件**: `vp-search/scripts/vp_page_ops.py`

- `_dismiss_confirm_dialog_if_present`: 改为先找到 `.layui-layer-dialog` 容器，再从容器内找 `.layui-layer-btn0`，单个短超时(500ms)快速判断，失败后 JS 兜底
- `_wait_for_dialog_dismissed`: 简化为单一 selector 等待，去掉冗余遍历和 `time.sleep(0.1)`

### 问题 2: 中断重启跳页

**根因**: `_jump_to_results_page` 中 `fill("")` + `sleep(0.1)` + `fill(str)` + `dispatch_event("input")` + `sleep(0.1)` 步骤冗余，且 `_wait_for_results_changed` + `_verify_page_arrived` 双重轮询。

**修复**: 删除不必要的 `time.sleep(0.1)` 调用，合并重复的页码验证逻辑。

**文件**: `vp-search/scripts/vp_navigation_ops.py`

- `_jump_to_results_page`: 删除 `fill("")` 前的 `time.sleep(0.1)` 和 `dispatch_event` 后的 `time.sleep(0.1)`，使用 `input_locator.fill(str(target_page))` 直接填值
- `_restore_results_position`: 删除 `_verify_page_arrived` 的冗余调用（`_jump_to_results_page` 内部已验证）

### 问题 3: 批量处理→导出题录不稳定

**根因**: `_click_export_entry_after_batch_action` 点击"批量处理"后用 `time.sleep(0.3)` 等待下拉菜单出现，但下拉菜单可能需要更长时间渲染，也可能瞬间就出现，固定等待不灵活。

**修复**: 用 `wait_for(state="visible")` 替代 `time.sleep`，用 hover 触发下拉菜单。

**文件**: `vp-search/scripts/vp_export_ops.py`

- `_click_export_entry_after_batch_action`:
  - 删除 `time.sleep(0.3)`
  - 点击"批量处理"后，用 `page.locator(".btn-hover-list a.behavior-exporttitle").first.wait_for(state="visible", timeout=3000)` 替代固定等待
  - 如果下拉菜单不出现，尝试 hover 触发

### 问题 4: 翻页与全选之间衔接太慢（核心优化）

**根因**: VP 的操作循环中存在大量冗余逻辑和过度防御性编程：

| 环节 | VP 当前 | CNKI 模式 | 差距来源 |
|------|---------|-----------|----------|
| `_select_batch_results` | 大量 timing 日志 + 二次采集复选框 | 简洁循环，直接用 `row_locator.count()` | 冗余日志/采集 |
| `_select_rows_on_current_page` | 4+ 阶段(收集→验证→全选→逐条→验证→补勾) | 3 阶段(全选/逐条→验证→补勾) | 多一次验证 |
| `_try_select_all_on_current_page` | 遍历 6 个 selector + Layui wrapper JS + 校验增量 | 直接点击 `#selectCheckAll1` | 过度复杂 |
| `_ensure_checkbox_checked` | 6+ 步(Layui JS→Layui click→原生 scroll→原生 check) | 3 步(check→scroll→check→JS fallback) | Layui 双策略 |
| `_current_page_checkbox_items` | 复杂的页码切片+可见性筛选+过滤+补齐 | 直接 `page.locator(selector)` | 完全多余 |
| `_goto_next_results_page` | `_wait_for_results_page_advanced` 50+ 行 | `_wait_for_results_changed` 15 行，0.5s 轮询 | 过度复杂 |
| `_wait_for_results_changed` | `_page_change_poll_interval_seconds()` (动态极小) | 固定 0.5s | 极小轮询浪费 CPU |
| `_wait_for_results_ready` | `max(0.05, _page_poll * 0.5)` 极小间隔 | 固定 0.5s | 同上 |
| `_extract_selected_count` | 遍历 8 个 selector | 直接 `#selectCount` | 过度兜底 |
| `_count_checked_rows` / `_find_unchecked_row_indexes` | 传入 `checkbox_items: list[Locator]` | 传入 `checkbox_locator: Locator`，用 `.nth(index)` | 传列表更重 |

**优化方案（参考 CNKI 模式）**:

#### 4a. 简化复选框采集 (`vp_checkbox_list_ops.py`)

- `_current_page_checkbox_items`: 大幅简化，直接用 `self.page.locator("input[name='selectArticle']")` 获取当前页结果行复选框，通过 `self._filter_result_row_checkbox_items()` 过滤掉页级全选框
- 删除 `_slice_checkbox_items_by_current_page`、`_collect_visible_result_row_checkboxes`、`_top_up_page_slice_rows`、`_is_reliable_page_slice` 等过度工程化的方法
- 保留 `_filter_result_row_checkbox_items`、`_is_result_row_checkbox`、`_resolve_checkbox_click_target`、`_is_checkbox_checked`（VP Layui UI 框架需要这些）

#### 4b. 简化批量勾选循环 (`vp_selection_ops.py`)

- `_select_batch_results`: 删除所有 `time.perf_counter()` timing 日志，删除二次采集 `time.sleep(1)` 重试逻辑，简化为 CNKI 风格的简洁循环
- `_select_rows_on_current_page`: 改为 CNKI 风格 3 阶段（全选/逐条 → 验证 → 补勾），参数改为 `checkbox_locator: Locator` 而非 `checkbox_items: list[Locator]`
- `_try_select_all_on_current_page`: 简化 selector 列表，直接点击 `input[name='selectArticleAll']` 对应的 Layui wrapper，删除 6 selector 遍历
- `_select_rows_incrementally`: 简化，删除 `target_cache`，减少重试中的 `time.sleep(0.1)` 和 `scrollBy`
- `_ensure_checkbox_checked` / `_ensure_checkbox_checked_with_target`: 合并为一个方法，简化为：先 check 原生 → 失败则 Layui wrapper JS → 失败则 Layui wrapper click → 失败则原生 JS 兜底
- `_extract_selected_count`: 使用用户提供的 HTML selector `span[data-topcount='topcount']` 作为首选，减少兜底 selector 数量
- `_clear_selected_results`: 删除 `time.sleep(self._action_poll_interval_seconds())`，参考 CNKI 用固定 0.3s
- `_clear_page_selection`: 删除 `time.sleep(0.2)`

#### 4c. 简化翻页等待 (`vp_navigation_ops.py`)

- `_wait_for_results_page_advanced`: 替换为 CNKI 风格的 `_wait_for_results_changed`，固定 0.5s 轮询，删除 50+ 行的复杂页码推进检测
- `_goto_next_results_page`: 使用简化的 `_wait_for_results_changed`，添加 `NEXT_PAGE_RETRY_DELAY = 1` 常量替代动态 `_page_change_poll_interval_seconds()`
- `_wait_for_results_changed`: 固定 0.5s 轮询替代动态极小间隔
- 删除 `_is_results_page_advanced`、`_mark_known_results_page`（不再需要）

#### 4d. 优化轮询间隔 (`vp_page_ops.py`)

- `_wait_for_results_ready`: 固定 0.5s 轮询替代 `max(0.05, _page_poll * 0.5)`
- `_action_poll_interval_seconds`: 保留但仅用于非关键等待，关键路径使用固定值
- `_page_change_poll_interval_seconds`: 保留兼容但关键路径不再使用
- `_click_first_available`: timeout 改为 500ms（参考 CNKI），替代 `_locator_wait_timeout_ms()`（约 166ms，太小）

#### 4e. 导出优化 (`vp_export_ops.py`)

- `_download_from_export_page`: 删除 `time.sleep(max(self._action_poll_interval_seconds(), 0.2))`，改为 `wait_for` 等待

## 修改文件清单

| 文件 | 变更类型 |
|------|----------|
| `vp-search/scripts/vp_selection_ops.py` | 重构（简化勾选逻辑） |
| `vp-search/scripts/vp_navigation_ops.py` | 重构（简化翻页等待） |
| `vp-search/scripts/vp_page_ops.py` | 修改（对话框+轮询优化） |
| `vp-search/scripts/vp_export_ops.py` | 修改（导出入口+下载优化） |
| `vp-search/scripts/vp_checkbox_list_ops.py` | 重构（简化复选框采集） |
| `vp-search/scripts/vp_search_interactor.py` | 修改（添加常量） |

## 验证方案

1. 运行现有测试: `python -m pytest tests/test_vp_interactor.py -v`
2. 手动测试: 使用 `cli.py` 对 VP 执行一次高级检索，观察日志时间戳验证步骤衔接速度
3. 对比验证: 检查 CNKI 测试 `tests/test_cnki_interactor.py` 确保未引入回归
