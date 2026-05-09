"""站点 CLI 自动续跑接入测试。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.script_loader import load_script_module


class SiteCliAutoResumeTestCase(unittest.TestCase):
    """验证三个站点 CLI 都接入了自动续跑入口。"""

    def test_cnki_run_command_should_delegate_to_auto_resume_helper(self) -> None:
        cli_module = load_script_module(
            ROOT_DIR / "cnki-search" / "scripts",
            "cli",
            "cnki_cli_auto_resume_module",
        )
        args = SimpleNamespace(command="advanced-search")
        config = object()

        with patch.object(cli_module, "run_with_auto_resume", return_value={"status": "success"}) as helper_mock:
            result = cli_module.run_command(args, config)

        self.assertEqual(result["status"], "success")
        helper_mock.assert_called_once()

    def test_vp_run_command_should_delegate_to_auto_resume_helper(self) -> None:
        cli_module = load_script_module(
            ROOT_DIR / "vp-search" / "scripts",
            "cli",
            "vp_cli_auto_resume_module",
        )
        args = SimpleNamespace(command="advanced-search")
        config = object()

        with patch.object(cli_module, "run_with_auto_resume", return_value={"status": "success"}) as helper_mock:
            result = cli_module.run_command(args, config)

        self.assertEqual(result["status"], "success")
        helper_mock.assert_called_once()

    def test_wanfang_run_command_should_delegate_to_auto_resume_helper(self) -> None:
        cli_module = load_script_module(
            ROOT_DIR / "wanfang-search" / "scripts",
            "cli",
            "wanfang_cli_auto_resume_module",
        )
        args = SimpleNamespace(command="advanced-search")
        config = object()

        with patch.object(cli_module, "run_with_auto_resume", return_value={"status": "success"}) as helper_mock:
            result = cli_module.run_command(args, config)

        self.assertEqual(result["status"], "success")
        helper_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
