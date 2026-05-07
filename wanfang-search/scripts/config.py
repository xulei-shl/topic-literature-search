"""万方检索配置。"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.search_config import BaseSearchConfig


@dataclass
class WanfangSearchConfig(BaseSearchConfig):
    """万方检索配置。"""

    OUTPUT_NAMESPACE: ClassVar[str] = "wanfang-search"

    home_url: str = "https://www.wanfangdata.com.cn/"
    advanced_search_url: str = "https://s.wanfangdata.com.cn/advanced-search/paper"
