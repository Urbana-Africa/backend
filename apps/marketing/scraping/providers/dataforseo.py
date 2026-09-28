import logging
from typing import Any, Dict, List, Optional
from django.conf import settings
from ..base import ScrapeProvider, request_with_retry

logger = logging.getLogger(__name__)


class DataForSEOProvider(ScrapeProvider):
    name = "dataforseo"
    can_search = True
    can_extract = False

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.login = config.get("login") or getattr(settings, "DATAFORSEO_LOGIN", "")
        self.password = config.get("password") or getattr(settings, "DATAFORSEO_PASSWORD", "")
        self.location_code = int(
            config.get("location_code")
            or getattr(settings, "DATAFORSEO_LOCATION_CODE", 2840)
        )
        self.base_url = "https://api.dataforseo.com/v3"

    def _auth(self):
        return (self.login, self.password)

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Dict[str, Any]]:
        if not self.login or not self.password:
            raise RuntimeError("DataForSEO login/password not configured")

        payload = [{
            "keyword": query,
            "location_code": int(kwargs.get("location_code") or self.location_code),
            "language_code": kwargs.get("language_code") or self.config.get("language_code") or "en",
            "depth": max_results,
        }]

        resp = request_with_retry(
            "post",
            f"{self.base_url}/serp/google/organic/live/advanced",
            auth=self._auth(),
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        # DataForSEO returns status_code 20000 on success at both levels.
        top_status = data.get("status_code")
        if top_status and top_status != 20000:
            raise RuntimeError(
                f"DataForSEO error {top_status}: {data.get('status_message', 'unknown')}"
            )

        results = []
        for task in data.get("tasks", []):
            task_status = task.get("status_code")
            if task_status and task_status != 20000:
                raise RuntimeError(
                    f"DataForSEO task error {task_status}: {task.get('status_message', 'unknown')}"
                )
            for result in task.get("result") or []:
                for item in result.get("items") or []:
                    # Skip ads, featured snippets, maps, etc.
                    if item.get("type") != "organic":
                        continue
                    url = item.get("url")
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

    def extract(self, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        # DataForSEO is search-only in this adapter.
        return None

    def health_check(self) -> Dict[str, Any]:
        ok = bool(self.login and self.password)
        return {"ok": ok, "name": self.name, "message": "Credentials present" if ok else "Missing credentials"}
