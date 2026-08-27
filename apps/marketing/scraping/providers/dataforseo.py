import json
import logging
import requests
from typing import Any, Dict, List, Optional
from django.conf import settings
from ..base import ScrapeProvider

logger = logging.getLogger(__name__)


class DataForSEOProvider(ScrapeProvider):
    name = "dataforseo"
    can_search = True
    can_extract = False

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.login = config.get("login") or getattr(settings, "DATAFORSEO_LOGIN", "")
        self.password = config.get("password") or getattr(settings, "DATAFORSEO_PASSWORD", "")
        self.base_url = "https://api.dataforseo.com/v3"

    def _auth(self):
        return (self.login, self.password)

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Dict[str, Any]]:
        if not self.login or not self.password:
            raise RuntimeError("DataForSEO login/password not configured")

        payload = [{
            "keyword": query,
            "location_code": kwargs.get("location_code", 2840),
            "language_code": kwargs.get("language_code", "en"),
            "depth": max_results,
            "se_type": "organic",
        }]

        try:
            resp = requests.post(
                f"{self.base_url}/serp/google/organic/live/advanced",
                auth=self._auth(),
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for task in data.get("tasks", []):
                for result in task.get("result", []):
                    for item in result.get("items", []):
                        url = item.get("url") or item.get("domain")
                        if url:
                            results.append({
                                "url": url,
                                "title": item.get("title", ""),
                                "snippet": item.get("description", ""),
                                "source": self.name,
                            })
                            if len(results) >= max_results:
                                return results
            return results
        except Exception as e:
            logger.error(f"DataForSEO search error: {e}")
            return []

    def extract(self, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        # DataForSEO is search-only in this adapter.
        return None

    def health_check(self) -> Dict[str, Any]:
        ok = bool(self.login and self.password)
        return {"ok": ok, "name": self.name, "message": "Credentials present" if ok else "Missing credentials"}
