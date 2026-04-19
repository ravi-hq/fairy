"""Periodic maintenance tasks.

Two tasks run on the existing Procrastinate worker:

- `purge_old_session_logs` (daily at 02:00 UTC): deletes AgentSession rows in
  terminal states older than 30 days, which cascades to their AgentSessionLog
  rows.
- `mark_stuck_sessions_failed` (every 5 minutes): flips sessions stuck in
  `running` for >15 minutes to `failed`. Uses AgentSession.updated_at
  (auto_now on every state transition) rather than AgentSessionLog.created_at
  because only the former is index-friendly at this scale (see migration 0013).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import close_old_connections
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app

from agent_on_demand.models import AgentSession

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
WATCHDOG_IDLE_MINUTES = 15
PURGE_BATCH_SIZE = 500
TERMINAL_STATUSES = ("completed", "failed", "terminated")


@procrastinate_app.periodic(cron="0 2 * * *", periodic_id="purge_old_session_logs")
@procrastinate_app.task(queue="maintenance", name="purge_old_session_logs", pass_context=False)
def purge_old_session_logs(timestamp: int) -> None:
    close_old_connections()
    try:
        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        total = 0
        while True:
            ids = list(
                AgentSession.objects.filter(
                    status__in=TERMINAL_STATUSES,
                    updated_at__lt=cutoff,
                ).values_list("id", flat=True)[:PURGE_BATCH_SIZE]
            )
            if not ids:
                break
            deleted, _ = AgentSession.objects.filter(id__in=ids).delete()
            total += deleted
        logger.info("purge_old_session_logs: deleted %d sessions", total)
    finally:
        close_old_connections()


@procrastinate_app.periodic(cron="*/5 * * * *", periodic_id="mark_stuck_sessions_failed")
@procrastinate_app.task(queue="maintenance", name="mark_stuck_sessions_failed", pass_context=False)
def mark_stuck_sessions_failed(timestamp: int) -> None:
    close_old_connections()
    try:
        cutoff = timezone.now() - timedelta(minutes=WATCHDOG_IDLE_MINUTES)
        updated = AgentSession.objects.filter(status="running", updated_at__lt=cutoff).update(
            status="failed", updated_at=timezone.now()
        )
        if updated:
            logger.warning(
                "mark_stuck_sessions_failed: flipped %d session(s) to failed",
                updated,
            )
    finally:
        close_old_connections()
