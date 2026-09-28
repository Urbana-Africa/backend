import logging
import re
import time
import requests
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from django.conf import settings
from ..base import ScrapeProvider, request_with_retry

logger = logging.getLogger(__name__)

SNAPSHOT_POLL_INTERVAL_S = 5
SNAPSHOT_TIMEOUT_S = 180


class BrightDataProvider(ScrapeProvider):
    name = "brightdata"
    can_search = False
    can_extract = True

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key") or getattr(settings, "BRIGHTDATA_API_KEY", "")
        # Bright Data renamed "zone" to "proxy" in the dashboard; the API
        # request field is still "zone". Accept either config/env spelling.
        self.zone = (
            config.get("zone") or config.get("proxy")
            or getattr(settings, "BRIGHTDATA_ZONE", "")
            or getattr(settings, "BRIGHTDATA_PROXY", "")
        )
        self.base_url = "https://api.brightdata.com"
        self.instagram_dataset_id = config.get(
            "instagram_dataset_id"
        ) or getattr(settings, "BRIGHTDATA_IG_DATASET_ID", "gd_l1vikfch901nx3by4")
        # Pages below this many chars of visible text get a second look for
        # block markers before we pay for Web Unlocker.
        self.min_content_chars = int(config.get("min_content_chars") or 300)

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

    def _poll_snapshot(self, snapshot_id: str, timeout_s: int = SNAPSHOT_TIMEOUT_S) -> Optional[List[Dict[str, Any]]]:
        """
        Sync /scrape requests auto-convert to async after ~60s server-side and
        return a snapshot_id (HTTP 202). Poll /progress until ready, then
        download /snapshot. Returns the record list or None.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                prog = requests.get(
                    f"{self.base_url}/datasets/v3/progress/{snapshot_id}",
                    headers=self._headers(),
                    timeout=30,
                )
                status = prog.json().get("status", "").lower()
            except Exception as e:
                logger.warning(f"Bright Data snapshot progress check failed for {snapshot_id}: {e}")
                status = ""

            if status in ("ready", "done"):
                snap = requests.get(
                    f"{self.base_url}/datasets/v3/snapshot/{snapshot_id}",
                    headers=self._headers(),
                    params={"format": "json"},
                    timeout=60,
                )
                snap.raise_for_status()
                return snap.json()
            if status in ("failed", "error", "dead"):
                logger.warning(f"Bright Data snapshot {snapshot_id} ended with status '{status}'")
                return None
            time.sleep(SNAPSHOT_POLL_INTERVAL_S)

        logger.warning(f"Bright Data snapshot {snapshot_id} timed out after {timeout_s}s")
        return None

    def _extract_instagram(self, url: str) -> Optional[Dict[str, Any]]:
        resp = request_with_retry(
            "post",
            f"{self.base_url}/datasets/v3/scrape",
            headers=self._headers(),
            params={"dataset_id": self.instagram_dataset_id, "format": "json"},
            json=[{"url": url}],
            timeout=130,
        )
        resp.raise_for_status()
        data = resp.json()

        # Over ~60s of scraping the sync endpoint returns a snapshot_id instead
        # of data (HTTP 202) — poll until the snapshot is ready.
        if resp.status_code == 202 or (isinstance(data, dict) and data.get("snapshot_id")):
            snapshot_id = data.get("snapshot_id") if isinstance(data, dict) else None
            data = self._poll_snapshot(snapshot_id) if snapshot_id else None

        if not isinstance(data, list) or not data:
            logger.warning(f"Bright Data Instagram returned no records for {url}: {data}")
            return None

        item = data[0]
        return {
            "url": url,
            "text": self._instagram_profile_text(item, url),
            "json": item,
            "source": self.name,
        }

    _BROWSER_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # Phrases typical of CAPTCHA walls / anti-bot interstitials / JS shells.
    _BLOCK_MARKERS = (
        "just a moment", "checking your browser", "verify you are human",
        "unusual traffic", "access denied", "attention required",
        "request blocked", "captcha", "cf-chl", "ray id",
        "enable javascript", "please enable javascript",
        "you need to enable javascript", "browser check",
    )

    def _direct_fetch(self, url: str) -> Optional[Dict[str, Any]]:
        """Plain HTTP fetch + HTML-to-text — free. Returns None when the page
        looks blocked/empty so the caller can escalate to Web Unlocker."""
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": self._BROWSER_UA},
                timeout=30,
            )
        except Exception as e:
            logger.info(f"Direct fetch failed for {url}: {e}")
            return None
        if resp.status_code != 200:
            logger.info(f"Direct fetch got HTTP {resp.status_code} for {url}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)

        # Escalate on real signals only: block/CAPTCHA walls and effectively
        # empty JS-shell pages. A small page with real content is fine.
        if len(text) < self.min_content_chars:
            haystack = f"{text} {resp.text[:3000]}".lower()
            if any(m in haystack for m in self._BLOCK_MARKERS):
                logger.info(f"Direct fetch for {url} hit a block page — escalating")
                return None
            if len(text) < 40:
                logger.info(f"Direct fetch for {url} returned empty shell ({len(text)} chars) — escalating")
                return None

        return {
            "url": url,
            "markdown": text,
            "source": "direct",
            "_free_call": True,
        }

    def _extract_web(self, url: str) -> Optional[Dict[str, Any]]:
        """Cheap→paid ladder: plain fetch first, Web Unlocker on failure."""
        direct = self._direct_fetch(url)
        if direct:
            return direct

        if not self.zone:
            raise RuntimeError(
                "Direct fetch failed and no Bright Data proxy/zone configured for fallback"
            )

        resp = request_with_retry(
            "post",
            f"{self.base_url}/request",
            headers=self._headers(),
            json={
                "zone": self.zone,
                "url": url,
                "format": "raw",
                "data_format": "markdown",
            },
            timeout=120,
        )
        resp.raise_for_status()
        return {
            "url": url,
            "markdown": resp.text,
            "source": self.name,
        }

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Dict[str, Any]]:
        raise NotImplementedError("Bright Data does not provide a managed search endpoint in this adapter")

    def extract(self, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("Bright Data API key not configured")
        if self._is_instagram_url(url):
            return self._extract_instagram(url)
        return self._extract_web(url)

    def health_check(self) -> Dict[str, Any]:
        # Zone is optional — web extraction falls back to a free direct fetch.
        ok = bool(self.api_key)
        return {
            "ok": ok,
            "name": self.name,
            "message": (
                "Credentials present" if ok else "Missing credentials"
            ) + ("" if self.zone else " — no proxy/zone, web pages will use direct fetch only"),
        }
