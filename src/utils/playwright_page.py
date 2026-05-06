"""Playwright 页面原子操作工具。"""

from __future__ import annotations

from typing import Optional

from playwright.sync_api import Locator, Page


def first_visible_locator(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
    error_cls: type[Exception],
    error_message: str,
) -> Locator:
    """返回首个可见定位器。"""
    for selector in selectors:
        locator_group = page.locator(selector)
        if locator_group.count() == 0:
            continue
        for index in range(locator_group.count()):
            locator = locator_group.nth(index)
            try:
                locator.wait_for(state="visible", timeout=timeout_ms)
                return locator
            except Exception:
                continue
    raise error_cls(error_message)


def click_first_available(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
) -> bool:
    """点击首个可见可点元素。"""
    for selector in selectors:
        locator_group = page.locator(selector)
        if locator_group.count() == 0:
            continue
        for index in range(locator_group.count()):
            locator = locator_group.nth(index)
            try:
                locator.wait_for(state="visible", timeout=timeout_ms)
                locator.click()
                return True
            except Exception:
                continue
    return False


def has_visible_selector(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
) -> bool:
    """判断任一选择器是否存在可见元素。"""
    for selector in selectors:
        locator_group = page.locator(selector)
        if locator_group.count() == 0:
            continue
        for index in range(locator_group.count()):
            locator = locator_group.nth(index)
            try:
                locator.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                continue
    return False


def wait_for_any_selector(
    page: Page,
    selectors: list[str],
    timeout_seconds: float,
    poll_interval_seconds: float,
    wait_timeout_ms: int,
    error_cls: type[Exception],
    error_message: str,
) -> None:
    """等待任一选择器出现。"""
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if has_visible_selector(page, selectors, wait_timeout_ms):
            return
        time.sleep(poll_interval_seconds)
    raise error_cls(error_message)


def set_input_value(locator: Locator, value: str) -> None:
    """为输入框写值并触发事件。"""
    locator.evaluate(
        """
        (element, inputValue) => {
            element.removeAttribute('readonly');
            element.value = inputValue;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
        value,
    )


def set_native_select_value(
    page: Page,
    selector: str,
    value: str,
    error_cls: type[Exception],
) -> None:
    """为原生下拉框写值并触发事件。"""
    locator = page.locator(selector).first
    if locator.count() == 0:
        raise error_cls(f"未找到年份选择器: {selector}")
    locator.evaluate(
        """
        (element, selectedValue) => {
            element.value = selectedValue;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
        value,
    )


def disable_checkbox(
    page: Page,
    selector: str,
    logger=None,
    verify_unchecked: bool = False,
) -> None:
    """取消复选框勾选。"""
    checkbox = page.locator(selector).first
    if checkbox.count() == 0:
        return
    try:
        if checkbox.is_checked():
            checkbox.evaluate(
                """
                (element) => {
                    element.checked = false;
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.dispatchEvent(new Event('click', { bubbles: true }));
                }
                """
            )
            try:
                checkbox.uncheck(force=True)
            except Exception:
                pass
            if verify_unchecked and checkbox.is_checked():
                raise RuntimeError(f"未能取消勾选控件: {selector}")
    except Exception as exc:
        if logger is not None:
            logger.debug("取消勾选失败: %s", exc)


def enable_checkbox(page: Page, selector: str, logger=None) -> None:
    """勾选复选框。"""
    checkbox = page.locator(selector).first
    if checkbox.count() == 0:
        return
    try:
        if not checkbox.is_checked():
            checkbox.check(force=True)
    except Exception as exc:
        if logger is not None:
            logger.debug("勾选复选框失败: %s", exc)
