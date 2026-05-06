"""维普检索配置。"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.search_config import BaseSearchConfig


@dataclass
class VpSearchConfig(BaseSearchConfig):
    """维普检索配置。"""

    OUTPUT_NAMESPACE: ClassVar[str] = "vp-search"

    home_url: str = "https://qikan.cqvip.com/"
    advanced_search_url: str = "https://qikan.cqvip.com/Qikan/Search/Advance?from=index"
