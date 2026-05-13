# Fix VP Search: Submit Button Click & Batch Selection Detection

## Context

Two bugs in `vp-search/` cause frequent retry failures:

1. **Submit button click timeouts**: `_submit_advanced_search` delegates to `click_first_available` → `locator.click()` without `force=True`. Playwright's actionability checks time out (15s) because a lingering layui dropdown overlay covers the button.

2. **Batch selection reports empty despite visible selections**: `_select_batch_results` relies solely on per-checkbox `_is_checkbox_checked` to count selections. When the Layui wrapper class detection fails (race condition, DOM structure mismatch), `selected_count` stays 0. But the UI indicator (`span[data-topcount]`) correctly shows 50 selected. The code doesn't consult this UI indicator as a fallback.

The sibling codebase `weipu-search/` has already solved both issues:
- `weipu_form_ops.py:203-214`: dismisses layui dropdowns, then uses `force=True` click
- `weipu_selection_ops.py:46-58,116-142`: uses `get_selected_count()` from `data-topcount` attribute as the primary verification mechanism

## Changes

### 1. `vp-search/scripts/vp_form_ops.py` — Fix submit button click

Add `_dismiss_layui_dropdowns()` helper and rewrite `_submit_advanced_search` to:
- Click body at (0,0) to close any lingering layui dropdown overlays
- Use `self.page.locator("button.behavior-advancesearch").first.click(force=True, timeout=self._action_timeout_ms())` instead of `_click_first_available`
- Keep the `_click_first_available` fallback for backward compatibility if `force=True` also fails

### 2. `vp-search/scripts/vp_selection_ops.py` — Add UI count fallback

In `_select_batch_results`, after the while loop (line 105), before the `if selected_count <= 0` check:
- Call `_extract_selected_count(0)` as a fallback verification
- If `selected_count` from per-checkbox counting is 0 but `_extract_selected_count(0) > 0`, use the extracted count instead
- This prevents the RuntimeError when the UI clearly shows selections but individual checkbox detection fails

### 3. `src/utils/playwright_page.py` — Add `force` and `no_wait_after` support (optional enhancement)

Add a `click_options` dict parameter to `click_first_available` so callers can pass `force=True` / `no_wait_after=True` without changing all call sites. This is optional — the primary fix is in vp_form_ops.py.

## Verification

- Run `vp-search` advanced-search with a test query and verify:
  1. The submit button click succeeds without timeout
  2. Batch selection proceeds past the first batch without the "批次勾选结果为空" error
- Compare behavior against `weipu-search` which already has stable versions of both patterns
