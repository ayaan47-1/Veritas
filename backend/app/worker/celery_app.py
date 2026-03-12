from __future__ import annotations

from celery import Celery

from ..config import settings

celery_app = Celery(
    "veritas",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_default_queue=settings.raw.get("celery", {}).get("queues", {}).get("default", "default"),
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

celery_app.autodiscover_tasks(["backend.app.worker.tasks"])
