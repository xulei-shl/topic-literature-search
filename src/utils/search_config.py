"""检索配置公共基类。"""

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.utils.output_path import ensure_namespaced_output_dir, resolve_project_root
from src.utils.result_output import build_output_slug
from src.utils.search_timeout import load_timeout_settings


@dataclass
class BaseSearchConfig:
    """检索配置公共基类。"""

    OUTPUT_NAMESPACE = "search"

    headless: bool = False
    geoip: bool = False
    proxy: Optional[str] = None
    language: str = "zh-CN"

    page_timeout: Optional[int] = None
    navigation_timeout: Optional[int] = None
    captcha_timeout: Optional[int] = None
    action_timeout: Optional[int] = None
    page_change_timeout: Optional[int] = None

    max_results: int = 100
    save_results: bool = True
    json_only: bool = False
    output_dir: Optional[Path] = None
    session_dir: Optional[Path] = None

    def __post_init__(self) -> None:
        """解析通用超时配置。"""
        timeout_settings = load_timeout_settings(
            anchor_file=self.module_file(),
            explicit_values={
                "PAGE_TIMEOUT": self.page_timeout,
                "NAVIGATION_TIMEOUT": self.navigation_timeout,
                "CAPTCHA_TIMEOUT": self.captcha_timeout,
                "ACTION_TIMEOUT": self.action_timeout,
                "PAGE_CHANGE_TIMEOUT": self.page_change_timeout,
            },
        )
        self.page_timeout = timeout_settings["PAGE_TIMEOUT"]
        self.navigation_timeout = timeout_settings["NAVIGATION_TIMEOUT"]
        self.captcha_timeout = timeout_settings["CAPTCHA_TIMEOUT"]
        self.action_timeout = timeout_settings["ACTION_TIMEOUT"]
        self.page_change_timeout = timeout_settings["PAGE_CHANGE_TIMEOUT"]

    def module_file(self) -> Path:
        """返回当前配置模块文件路径。"""
        module = sys.modules[self.__class__.__module__]
        return Path(module.__file__).resolve()

    def skill_root(self) -> Path:
        """返回技能根目录。"""
        return self.module_file().parent.parent

    def project_root(self) -> Path:
        """返回仓库根目录。"""
        return resolve_project_root(self.module_file())

    def ensure_output_dir(self, data: Optional[dict[str, Any] | str] = None) -> Path:
        """确保输出目录存在。"""
        slug = build_output_slug(data, self.OUTPUT_NAMESPACE) if data else None
        return ensure_namespaced_output_dir(
            anchor_file=self.module_file(),
            namespace=self.OUTPUT_NAMESPACE,
            slug=slug,
            explicit_output_dir=self.output_dir,
        )

    def ensure_session_dir(self) -> Path:
        """确保会话目录存在。"""
        session_dir = self.session_dir or self.skill_root() / "data" / "session"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    @classmethod
    def get_headless_mode(cls, headless: bool) -> bool | str:
        """智能决定无头模式。"""
        if headless is True:
            return True

        if platform.system() == "Linux" and not os.environ.get("DISPLAY"):
            if shutil.which("Xvfb") is not None:
                return "virtual"
            return True

        return headless
