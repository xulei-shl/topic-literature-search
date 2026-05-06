"""测试使用的脚本模块加载工具。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

COMMON_SCRIPT_MODULES = {
    "advanced_export_ops",
    "browser",
    "cli",
    "config",
    "exceptions",
    "export_processor",
    "interactor",
    "progress_store",
    "result_parser",
    "utils",
}


def load_script_module(script_dir: Path, module_name: str, alias: str):
    """按文件路径加载脚本模块，避免同名模块互相污染。"""
    resolved_dir = script_dir.resolve()
    script_dir_str = str(resolved_dir)
    if script_dir_str in sys.path:
        sys.path.remove(script_dir_str)
    sys.path.insert(0, script_dir_str)
    for common_name in COMMON_SCRIPT_MODULES:
        loaded_module = sys.modules.get(common_name)
        loaded_file = getattr(loaded_module, "__file__", "")
        if loaded_file:
            try:
                if Path(loaded_file).resolve().parent == resolved_dir:
                    continue
            except OSError:
                pass
        sys.modules.pop(common_name, None)

    module_path = script_dir / f"{module_name}.py"
    spec = spec_from_file_location(alias, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {module_path}")

    module = module_from_spec(spec)
    sys.modules[alias] = module
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
