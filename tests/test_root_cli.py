"""顶层高级检索 CLI 测试。"""

import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]


def load_root_cli_module():
    """按文件路径加载顶层 CLI 模块。"""
    module_name = "root_cli_test_module"
    module_path = ROOT_DIR / "cli.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeProcess:
    """模拟子进程对象。"""

    def __init__(self, return_code: int) -> None:
        self.return_code = return_code

    async def wait(self) -> int:
        """返回预设退出码。"""
        return self.return_code


class RootCliTestCase(unittest.TestCase):
    """验证顶层 CLI 的调度行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载待测模块。"""
        cls.module = load_root_cli_module()

    def test_parse_cli_args_should_preserve_passthrough_options(self) -> None:
        """顶层 CLI 应保留高级检索透传参数。"""
        args, passthrough_args = self.module.parse_cli_args(
            [
                "advanced-search",
                "--all",
                "--query",
                "新青年",
                "--date-from",
                "1915",
                "-n",
                "100",
            ]
        )

        self.assertEqual(args.command, "advanced-search")
        self.assertTrue(args.run_all)
        self.assertEqual(self.module.resolve_targets(args), ["cnki", "vp", "wanfang"])
        self.assertEqual(
            passthrough_args,
            ["--query", "新青年", "--date-from", "1915", "-n", "100"],
        )

    def test_resolve_targets_should_reject_mixed_all_and_specific_targets(self) -> None:
        """`--all` 不能与显式目标同时出现。"""
        args, _ = self.module.parse_cli_args(["advanced-search", "--all", "--cnki"])

        with self.assertRaisesRegex(ValueError, "--all"):
            self.module.resolve_targets(args)

    def test_run_selected_targets_should_not_cancel_other_tasks_when_one_spawn_fails(self) -> None:
        """某一路启动失败时，其余目标仍应继续执行。"""
        spawn_commands: list[tuple[tuple[str, ...], dict[str, object]]] = []

        async def fake_create_subprocess_exec(*command, **kwargs):
            spawn_commands.append((command, kwargs))
            command_text = " ".join(command)
            if "vp-search" in command_text:
                raise OSError("vp 启动失败")
            if "wanfang-search" in command_text:
                return FakeProcess(2)
            return FakeProcess(0)

        with patch.object(self.module.asyncio, "create_subprocess_exec", new=fake_create_subprocess_exec):
            results = asyncio.run(
                self.module.run_selected_targets(
                    ["cnki", "vp", "wanfang"],
                    ["--query", "新青年"],
                )
            )

        self.assertEqual(len(spawn_commands), 3)
        result_mapping = {result.target: result for result in results}
        self.assertEqual(result_mapping["cnki"].exit_code, 0)
        self.assertEqual(result_mapping["vp"].exit_code, 1)
        self.assertIn("vp 启动失败", result_mapping["vp"].error_message)
        self.assertEqual(result_mapping["wanfang"].exit_code, 2)

    def test_main_should_return_zero_when_target_succeeds(self) -> None:
        """单目标成功时顶层入口应返回 0。"""

        async def fake_create_subprocess_exec(*command, **kwargs):
            del command, kwargs
            return FakeProcess(0)

        with patch.object(self.module.asyncio, "create_subprocess_exec", new=fake_create_subprocess_exec):
            with patch("builtins.print"):
                exit_code = self.module.main(["advanced-search", "--cnki", "--query", "新青年"])

        self.assertEqual(exit_code, 0)

    def test_main_should_return_one_when_no_target_selected(self) -> None:
        """未指定执行目标时应返回参数错误。"""
        with patch("builtins.print"):
            exit_code = self.module.main(["advanced-search", "--query", "新青年"])

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
