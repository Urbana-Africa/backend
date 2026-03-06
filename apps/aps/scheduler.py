# apps/pay/scheduler.py

from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
from django_apscheduler.jobstores import DjangoJobStore, register_events
import logging

from .tasks import (
    auto_release_escrows_after_24hrs,
    create_escrows_for_successful_payments,
    release_escrows_for_received_items,
)

logger = logging.getLogger(__name__)


def start():
    scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)
    scheduler.add_jobstore(DjangoJobStore(), "default")

    if settings.ENV == "dev":
        interval_minutes = 1
    else:
        interval_minutes = 5  # production safety delay

    # -------------------------------------------------------
    # Create Escrows
    # -------------------------------------------------------
    scheduler.add_job(
        create_escrows_for_successful_payments,
        trigger="interval",
        minutes=interval_minutes,
        id="create_escrows_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # -------------------------------------------------------
    # Release Escrows
    # -------------------------------------------------------
    scheduler.add_job(
        release_escrows_for_received_items,
        trigger="interval",
        minutes=interval_minutes,
        id="release_escrows_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # -------------------------------------------------------
    # Auto Release Escrows
    # -------------------------------------------------------
    scheduler.add_job(
        auto_release_escrows_after_24hrs,
        trigger="interval",
        minutes=interval_minutes,
        id="auto_release_escrows_after_24hrs_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    register_events(scheduler)
    scheduler.start()

    logger.info("Escrow scheduler started successfully.")