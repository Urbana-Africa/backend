from .base import ScrapeProvider
from .providers.dataforseo import DataForSEOProvider
from .providers.brightdata import BrightDataProvider


PROVIDERS = {
    "dataforseo": DataForSEOProvider,
    "brightdata": BrightDataProvider,
}


def get_provider(name: str, config: dict) -> ScrapeProvider:
    klass = PROVIDERS.get(name.lower())
    if not klass:
        raise ValueError(f"Unknown scrape provider: {name}")
    return klass(config)


def list_providers() -> list:
    return list(PROVIDERS.keys())
