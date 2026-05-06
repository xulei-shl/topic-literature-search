"""输出目录路径工具。"""

from pathlib import Path
from typing import Optional

ROOT_MARKERS = ("src", ".rules")
DEFAULT_FALLBACK_LEVELS = 2


def resolve_project_root(anchor_file: Path, fallback_levels: int = DEFAULT_FALLBACK_LEVELS) -> Path:
    """解析仓库根目录。

    Args:
        anchor_file: 当前模块文件路径。
        fallback_levels: 未命中根目录标记时使用的向上回退层级。

    Returns:
        Path: 解析后的仓库根目录路径。

    Raises:
        ValueError: 回退层级为负数时抛出。
    """
    if fallback_levels < 0:
        raise ValueError("fallback_levels 不能小于 0")

    current_file = anchor_file.resolve()
    for path in current_file.parents:
        if all((path / marker).exists() for marker in ROOT_MARKERS):
            return path

    fallback_index = min(fallback_levels, len(current_file.parents) - 1)
    return current_file.parents[fallback_index]


def ensure_namespaced_output_dir(
    anchor_file: Path,
    namespace: str,
    slug: Optional[str] = None,
    explicit_output_dir: Optional[Path] = None,
) -> Path:
    """确保带命名空间的输出目录存在。

    Args:
        anchor_file: 当前模块文件路径。
        namespace: 输出目录命名空间。
        slug: 可选的业务子目录名。
        explicit_output_dir: 显式传入的输出目录，优先级最高。

    Returns:
        Path: 最终输出目录路径。
    """
    if explicit_output_dir:
        output_dir = explicit_output_dir
    else:
        output_dir = resolve_project_root(anchor_file) / "outputs" / namespace
        if slug:
            output_dir = output_dir / slug

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
