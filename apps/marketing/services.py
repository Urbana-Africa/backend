import logging

logger = logging.getLogger(__name__)


def run_scraping_job(query: str, max_results: int = 5, created_by=None, provider_name: str = ""):
    """
    Creates a ScrapeJob and queues it for the APScheduler-backed provider engine.
    """
    from .models import ScrapeJob

    logger.info(f"Queueing scraping job for query: {query}")
    job = ScrapeJob.objects.create(
        query=query,
        max_results=max_results,
        created_by=created_by,
        provider_name=provider_name,
        status='queued'
    )

    return job
