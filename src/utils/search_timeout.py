"""检索脚本通用超时配置工具。"""

import os
from pathlib import Path
from typing import Optional

TIMEOUT_ENV_DEFAULTS = {
    "PAGE_TIMEOUT": 30,
    "NAVIGATION_TIMEOUT": 30,
    "CAPTCHA_TIMEOUT": 120,
    "ACTION_TIMEOUT": 10,
    "PAGE_CHANGE_TIMEOUT": 90,
}


def find_env_path(anchor_file: Path, cwd: Optional[Path] = None) -> Path:
    """向上查找 `.env` 文件。

    Args:
        anchor_file: 当前配置文件路径。
        cwd: 可选当前工作目录，默认使用 `Path.cwd()`。

    Returns:
        Path: 找到的 `.env` 路径；未找到时返回当前工作目录下的候选路径。
    """
    candidate_dirs: list[Path] = []
    current_dir = (cwd or Path.cwd()).resolve()
    script_dir = anchor_file.resolve().parent

    for base_dir in (current_dir, script_dir):
        candidate_dirs.extend([base_dir, *base_dir.parents])

    seen_paths: set[Path] = set()
    for directory in candidate_dirs:
        if directory in seen_paths:
            continue
        seen_paths.add(directory)

        env_path = directory / ".env"
        if env_path.exists():
            return env_path

    return current_dir / ".env"


def parse_env_file(env_path: Path) -> dict[str, str]:
    """解析 `.env` 文件内容。

    Args:
        env_path: `.env` 文件路径。

    Returns:
        dict[str, str]: 解析得到的键值对。
    """
    if not env_path.exists():
        return {}

    env_values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env_values[key] = value
    return env_values


def resolve_timeout_setting(
    explicit_value: Optional[int],
    env_key: str,
    default_value: int,
    env_values: Optional[dict[str, str]] = None,
) -> int:
    """解析单个超时配置。

    优先级：显式值 > 进程环境变量 > `.env` 文件 > 默认值。

    Args:
        explicit_value: 显式传入值。
        env_key: 对应环境变量键名。
        default_value: 默认值。
        env_values: `.env` 解析结果。

    Returns:
        int: 最终超时值。
    """
    if explicit_value is not None:
        if explicit_value <= 0:
            raise ValueError(f"{env_key} 必须大于 0")
        return explicit_value

    raw_value = os.environ.get(env_key, "").strip()
    if not raw_value and env_values:
        raw_value = env_values.get(env_key, "").strip()
    if not raw_value:
        return default_value

    try:
        timeout_value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{env_key} 必须是正整数，当前值: {raw_value}") from exc

    if timeout_value <= 0:
        raise ValueError(f"{env_key} 必须大于 0，当前值: {raw_value}")
    return timeout_value


def load_timeout_settings(
    anchor_file: Path,
    explicit_values: Optional[dict[str, Optional[int]]] = None,
) -> dict[str, int]:
    """统一加载检索脚本超时配置。

    Args:
        anchor_file: 当前配置文件路径。
        explicit_values: 显式传入的超时配置。

    Returns:
        dict[str, int]: 解析后的超时配置。
    """
    env_values = parse_env_file(find_env_path(anchor_file))
    explicit_values = explicit_values or {}

    return {
        env_key: resolve_timeout_setting(
            explicit_value=explicit_values.get(env_key),
            env_key=env_key,
            default_value=default_value,
            env_values=env_values,
        )
        for env_key, default_value in TIMEOUT_ENV_DEFAULTS.items()
    }
