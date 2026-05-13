# Fix VP Export Entry: Multi-Variant Batch Action Fallback

## Context

The VP export flow fails with `点击批量处理按钮失败` and eventually `未找到"导出题录"入口` because `_click_export_entry_after_batch_action()` stops at the first batch action selector that exists in DOM (`count() > 0`), rather than iterating all selectors to find one that is *interactable* and *yields an export entry*.

The VP results page has three UI variants for the export entry:
1. **Dropdown**: `<span class="behavior-allDowns">` — hover opens a `<dl>` dropdown containing `<a class="behavior-exporttitle">导出题录</a>`
2. **Sidebar**: `<a href="javascript:batch();">` inside `<div class="search-tools">` — click opens a batch panel with export options
3. **Direct**: `<a class="behavior-exporttitle">导出题录</a>` visible on the page (handled by the first path in `_click_export_entry`)

Any one working is sufficient. The current code picks the first DOM match and gives up, missing the sidebar fallback.

## Fix

**Single file change**: `vp-search/scripts/vp_export_ops.py`

Rewrite `_click_export_entry_after_batch_action()` (lines 118-171) with a two-phase loop over ALL `BATCH_ACTION_MENU_SELECTORS`:

1. **Activation phase** — For each selector, try three strategies in order:
   - `hover()` — for CSS-triggered dropdowns
   - `click()` — for sidebar links  
   - `evaluate("element.click()")` — JS bypass for elements Playwright can't interact with

2. **Harvest phase** — After activation, poll for export entry in dropdown/panel:
   - `batch_menu_export_selectors` (dropdown export links)
   - `combined_export_selectors` (direct + menu fallback)
   - `EXPORT_BATCH_DIALOG_SELECTORS` (dialog buttons)

3. If one batch trigger path fails (activation or harvest), continue to the next selector.

## Key behavioral changes

| Before | After |
|--------|-------|
| First `count() > 0` selector wins, fail fast | Iterates ALL selectors, skip only `count() == 0` |
| Hover → click, then give up | Hover → click → JS evaluate, then skip to next |
| No JS fallback | `element.click()` via evaluate (already used in `vp_navigation_ops.py:310` and `vp_export_ops.py:330`) |
| `return False` on first failure | Continue to next selector |

## Verification

1. Read the modified file to confirm the logic is correct
2. Run existing tests: `python -m pytest tests/test_vp_interactor.py -v` (the relevant test at line 610 mocks `_click_first_available`, so no new tests needed)
3. The fix is self-contained — all selectors and helper methods remain unchanged
