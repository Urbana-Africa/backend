from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class ScrapeProvider(ABC):
    """
    Abstract base for all third-party scraping/search providers.
    """
    name: str = ""
    can_search: bool = False
    can_extract: bool = False

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Dict[str, Any]]:
        """
        Return a list of result dicts, each with at least an 'url' key.
        """
        pass

    @abstractmethod
    def extract(self, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Return a structured dict extracted from a single URL.
        """
        pass

    def health_check(self) -> Dict[str, Any]:
        """
        Quick check that the provider is configured and reachable.
        """
        return {"ok": True, "name": self.name, "message": "No health check implemented."}
