import time
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

import requests

TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


def request_with_retry(method: str, url: str, *, attempts: int = 3, backoff: tuple = (2, 6), **kwargs) -> requests.Response:
    """
    requests wrapper with retry on transient failures (timeouts, connection
    errors, 429/5xx). Returns the last response (even a failed status) so the
    caller can decide how to handle 4xx.
    """
    resp = None
    for i in range(attempts):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code not in TRANSIENT_STATUSES or i == attempts - 1:
                return resp
        except (requests.ConnectionError, requests.Timeout):
            if i == attempts - 1:
                raise
        time.sleep(backoff[min(i, len(backoff) - 1)])
    return resp


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
