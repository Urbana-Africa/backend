import logging
import sys
import os
from django.apps import AppConfig

logger = logging.getLogger(__name__)

class ApsConfig(AppConfig):
    name = "apps.aps"

    def ready(self):
        import threading

        # Skip commands that shouldn't start the scheduler
        if any(cmd in sys.argv for cmd in ["migrate", "makemigrations", "collectstatic", "shell", "test"]):
            return

        # In development with auto-reload, only start in the reloader process
        # (the main process manages the reloader and doesn't run app code)
        if "runserver" in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        logger.info("Starting APScheduler (DjangoJobStore)")
        threading.Thread(target=self._start_scheduler, daemon=True).start()

    def _start_scheduler(self):
        from .scheduler import start
        start()