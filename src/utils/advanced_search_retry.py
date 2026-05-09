"""高级检索自动续跑工具。"""

import time
from collections.abc import Callable
from typing import Any, Optional

DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_RETRY_DELAY_SECONDS = 2


def run_with_auto_resume(
    *,
    args: Any,
    run_once: Callable[[], dict[str, Any]],
    non_retryable_error_types: tuple[type[BaseException], ...],
    logger: Optional[Any],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
) -> dict[str, Any]:
    """在高级检索场景下执行自动续跑。

    Args:
        args: 命令行参数对象，至少需要包含 `command` 字段。
        run_once: 单次执行函数，内部负责浏览器生命周期。
        non_retryable_error_types: 兼容保留参数，当前高级检索场景下不再区分异常类型。
        logger: 可选日志对象。
        max_attempts: 最大执行次数，包含首次执行。
        retry_delay_seconds: 两次执行之间的等待秒数。

    Returns:
        dict[str, Any]: 单次或最终成功执行的结果。

    Raises:
        BaseException: 非可重试异常或达到重试上限时抛出原始异常。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 必须大于等于 1")

    if getattr(args, "command", None) != "advanced-search":
        return run_once()

    attempt = 1
    while True:
        try:
            return run_once()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if attempt >= max_attempts:
                if logger is not None:
                    logger.exception(
                        "高级检索自动续跑达到最大执行次数: attempt=%s/%s",
                        attempt,
                        max_attempts,
                    )
                raise

            attempt += 1
            if logger is not None:
                logger.warning(
                    "高级检索执行失败，准备自动续跑: attempt=%s/%s, error=%s",
                    attempt,
                    max_attempts,
                    exc,
                )
            print(f"\n高级检索执行失败，准备关闭浏览器并基于进度文件自动续跑（第 {attempt}/{max_attempts} 次执行）")
            time.sleep(retry_delay_seconds)
