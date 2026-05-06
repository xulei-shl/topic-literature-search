"""CNKI 检索配置。"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.search_config import BaseSearchConfig


@dataclass
class CnkiSearchConfig(BaseSearchConfig):
    """CNKI 检索配置。"""

    OUTPUT_NAMESPACE: ClassVar[str] = "cnki-search"

    home_url: str = "https://www.cnki.net/"
    search_url: str = "https://kns.cnki.net/kns8s/search"
    advanced_search_url: str = "https://kns.cnki.net/kns8s/AdvSearch?classid=YSTT4HG0"
