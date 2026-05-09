"""高级检索自动续跑测试。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.utils.advanced_search_retry import run_with_auto_resume


class AdvancedSearchRetryTestCase(unittest.TestCase):
    """验证高级检索自动续跑行为。"""

    def test_non_advanced_search_should_not_retry(self) -> None:
        """非高级检索命令不应启用自动续跑。"""
        args = SimpleNamespace(command="login")
        calls = {"count": 0}

        def run_once():
            calls["count"] += 1
            return {"status": "success"}

        result = run_with_auto_resume(
            args=args,
            run_once=run_once,
            non_retryable_error_types=(ValueError,),
            logger=None,
        )

        self.assertEqual(calls["count"], 1)
        self.assertEqual(result["status"], "success")

    def test_advanced_search_should_retry_until_success(self) -> None:
        """高级检索遇到运行时异常后应自动重试。"""
        args = SimpleNamespace(command="advanced-search")
        calls = {"count": 0}

        def run_once():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("瞬时失败")
            return {"status": "success"}

        with (
            patch("builtins.print") as mock_print,
            patch("src.utils.advanced_search_retry.time.sleep", return_value=None),
        ):
            result = run_with_auto_resume(
                args=args,
                run_once=run_once,
                non_retryable_error_types=(ValueError,),
                logger=None,
                max_attempts=3,
            )

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result["status"], "success")
        mock_print.assert_any_call("\n高级检索执行失败，准备关闭浏览器并基于进度文件自动续跑（第 2/3 次执行）")

    def test_advanced_search_should_retry_value_error_too(self) -> None:
        """高级检索下应对所有异常一视同仁地自动重试。"""
        args = SimpleNamespace(command="advanced-search")
        calls = {"count": 0}

        def run_once():
            calls["count"] += 1
            if calls["count"] == 1:
                raise ValueError("参数错误")
            return {"status": "success"}

        with (
            patch("builtins.print") as mock_print,
            patch("src.utils.advanced_search_retry.time.sleep", return_value=None),
        ):
            result = run_with_auto_resume(
                args=args,
                run_once=run_once,
                non_retryable_error_types=(ValueError,),
                logger=None,
                max_attempts=3,
            )

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result["status"], "success")
        mock_print.assert_any_call("\n高级检索执行失败，准备关闭浏览器并基于进度文件自动续跑（第 2/3 次执行）")


if __name__ == "__main__":
    unittest.main()
