import json
import logging
import re
import requests
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional
from django.conf import settings
from ..base import ScrapeProvider

logger = logging.getLogger(__name__)


class BrightDataProvider(ScrapeProvider):
    name = "brightdata"
    can_search = False
    can_extract = True

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key") or getattr(settings, "BRIGHTDATA_API_KEY", "")
        self.zone = config.get("zone") or getattr(settings, "BRIGHTDATA_ZONE", "")
        self.customer_id = config.get("customer_id") or getattr(settings, "BRIGHTDATA_CUSTOMER_ID", "")
        self.base_url = "https://api.brightdata.com"
        self.instagram_dataset_id = config.get(
            "instagram_dataset_id"
        ) or getattr(settings, "BRIGHTDATA_IG_DATASET_ID", "gd_l1vikfch901nx3by4")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _is_instagram_url(self, url: str) -> bool:
        return bool(urlparse(url).netloc and "instagram.com" in urlparse(url).netloc.lower())

    def _extract_email(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"[\w.-]+@[\w.-]+\.[\w]{2,}", text)
        return match.group(0) if match else None

    def _instagram_profile_text(self, item: Dict[str, Any], url: str) -> str:
        """Turn Bright Data's Instagram profile JSON into plain text for Gemini."""
        account = item.get("account", "")
        full_name = item.get("full_name", "") or account
        biography = item.get("biography", "") or ""
        followers = item.get("followers", 0)
        following = item.get("following", 0)
        posts_count = item.get("posts_count", 0)
        external_url = item.get("external_url") or ""
        email = self._extract_email(biography) or ""

        lines = [
            f"Instagram profile: @{account}",
            f"Name: {full_name}",
            f"Biography: {biography}",
            f"Followers: {followers}",
            f"Following: {following}",
            f"Posts: {posts_count}",
        ]
        if external_url:
            lines.append(f"Website: {external_url}")
        if email:
            lines.append(f"Email: {email}")

        return "\n".join(lines)

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Dict[str, Any]]:
        raise NotImplementedError("Bright Data does not provide a managed search endpoint in this adapter")

    def extract(self, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("Bright Data API key not configured")

        if self._is_instagram_url(url):
            try:
                resp = requests.post(
                    f"{self.base_url}/datasets/v3/scrape",
                    headers=self._headers(),
                    params={"dataset_id": self.instagram_dataset_id, "format": "json"},
                    json=[{"url": url}],
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()

                if not isinstance(data, list) or not data:
                    # Bright Data may return a snapshot_id dict on timeout; not handled here.
                    logger.warning(f"Bright Data Instagram returned non-list response for {url}: {data}")
                    return None

                item = data[0]
                return {
                    "url": url,
                    "text": self._instagram_profile_text(item, url),
                    "json": item,
                    "source": self.name,
                }
            except Exception as e:
                logger.error(f"Bright Data Instagram extract error: {e}")
                return None

        # Fallback: generic Bright Data web scraping (raw HTML).
        try:
            resp = requests.get(
                f"{self.base_url}/request",
                headers=self._headers(),
                params={
                    "customer": self.customer_id,
                    "zone": self.zone,
                    "url": url,
                    "format": "raw",
                },
                timeout=120,
            )
            resp.raise_for_status()
            return {
                "url": url,
                "html": resp.text,
                "source": self.name,
            }
        except Exception as e:
            logger.error(f"Bright Data extract error: {e}")
            return None

    def health_check(self) -> Dict[str, Any]:
        ok = bool(self.api_key and (self.zone or self.customer_id or self.instagram_dataset_id))
        return {
            "ok": ok,
            "name": self.name,
            "message": "Credentials present" if ok else "Missing credentials",
        }
