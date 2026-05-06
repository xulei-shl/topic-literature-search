"""检索超时配置测试。"""

import importlib.util
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

TIMEOUT_ENV_KEYS = [
    "PAGE_TIMEOUT",
    "NAVIGATION_TIMEOUT",
    "CAPTCHA_TIMEOUT",
    "ACTION_TIMEOUT",
    "PAGE_CHANGE_TIMEOUT",
]


@contextmanager
def temporary_cwd(target_dir: Path):
    """临时切换当前工作目录。"""
    original_cwd = Path.cwd()
    os.chdir(target_dir)
    try:
        yield
    finally:
        os.chdir(original_cwd)


def load_module(module_name: str, module_path: Path):
    """从文件路径加载模块。"""
    original_sys_path = list(sys.path)
    sys.modules.pop(module_name, None)
    sys.modules.pop("utils", None)
    sys.path.insert(0, str(module_path.parent))

    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = original_sys_path
        sys.modules.pop("utils", None)


class SearchTimeoutConfigTestCase(unittest.TestCase):
    """验证检索脚本超时配置读取行为。"""

    def setUp(self) -> None:
        """清理通用超时环境变量。"""
        self.original_env: dict[str, str] = {}
        for key in TIMEOUT_ENV_KEYS:
            original_value = os.environ.pop(key, None)
            if original_value is not None:
                self.original_env[key] = original_value

    def tearDown(self) -> None:
        """恢复通用超时环境变量。"""
        for key in TIMEOUT_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self.original_env)

    def test_cnki_and_vp_config_can_read_timeout_from_dotenv(self) -> None:
        """CNKI 与维普配置都应读取同一组通用超时变量。"""
        module_cases = [
            (
                "cnki_config_test",
                ROOT_DIR / "cnki-search" / "scripts" / "config.py",
                "CnkiSearchConfig",
            ),
            (
                "vp_config_test",
                ROOT_DIR / "vp-search" / "scripts" / "config.py",
                "VpSearchConfig",
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "PAGE_TIMEOUT=45",
                        "NAVIGATION_TIMEOUT=55",
                        "CAPTCHA_TIMEOUT=180",
                        "ACTION_TIMEOUT=15",
                        "PAGE_CHANGE_TIMEOUT=120",
                    ]
                ),
                encoding="utf-8",
            )

            with temporary_cwd(Path(temp_dir)):
                for module_name, module_path, class_name in module_cases:
                    with self.subTest(module=class_name):
                        module = load_module(module_name, module_path)
                        config_class = getattr(module, class_name)
                        config = config_class()

                        self.assertEqual(config.page_timeout, 45)
                        self.assertEqual(config.navigation_timeout, 55)
                        self.assertEqual(config.captcha_timeout, 180)
                        self.assertEqual(config.action_timeout, 15)
                        self.assertEqual(config.page_change_timeout, 120)

    def test_explicit_timeout_should_override_dotenv(self) -> None:
        """显式传入的超时配置应优先于 .env。"""
        module = load_module("cnki_config_explicit_test", ROOT_DIR / "cnki-search" / "scripts" / "config.py")
        config_class = getattr(module, "CnkiSearchConfig")

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("PAGE_TIMEOUT=45\nACTION_TIMEOUT=15\n", encoding="utf-8")

            with temporary_cwd(Path(temp_dir)):
                config = config_class(page_timeout=12, action_timeout=6)

        self.assertEqual(config.page_timeout, 12)
        self.assertEqual(config.action_timeout, 6)

    def test_invalid_timeout_should_raise_value_error(self) -> None:
        """非法超时值应抛出明确异常。"""
        module = load_module("vp_config_invalid_test", ROOT_DIR / "vp-search" / "scripts" / "config.py")
        config_class = getattr(module, "VpSearchConfig")

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("PAGE_TIMEOUT=abc\n", encoding="utf-8")

            with temporary_cwd(Path(temp_dir)):
                with self.assertRaisesRegex(ValueError, "PAGE_TIMEOUT"):
                    config_class()

    def test_process_env_should_override_dotenv(self) -> None:
        """进程环境变量应优先于 `.env` 文件。"""
        module = load_module("cnki_config_env_override_test", ROOT_DIR / "cnki-search" / "scripts" / "config.py")
        config_class = getattr(module, "CnkiSearchConfig")

        os.environ["PAGE_TIMEOUT"] = "66"
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("PAGE_TIMEOUT=45\n", encoding="utf-8")

            with temporary_cwd(Path(temp_dir)):
                config = config_class()

        self.assertEqual(config.page_timeout, 66)

    def test_cnki_and_vp_output_dir_should_use_workspace_outputs(self) -> None:
        """CNKI 与维普结果都应写入仓库根目录下的 outputs。"""
        workspace_root = ROOT_DIR.resolve()
        module_cases = [
            (
                "cnki_config_output_test",
                ROOT_DIR / "cnki-search" / "scripts" / "config.py",
                "CnkiSearchConfig",
                "cnki-search",
            ),
            (
                "vp_config_output_test",
                ROOT_DIR / "vp-search" / "scripts" / "config.py",
                "VpSearchConfig",
                "vp-search",
            ),
        ]

        for module_name, module_path, class_name, namespace in module_cases:
            with self.subTest(module=class_name):
                module = load_module(module_name, module_path)
                config_class = getattr(module, class_name)
                config = config_class()

                self.assertEqual(config.project_root(), workspace_root)
                self.assertEqual(
                    config.ensure_output_dir("新青年"),
                    workspace_root / "outputs" / namespace / "新青年",
                )


if __name__ == "__main__":
    unittest.main()
