"""Procrastinate task auto-discovery entry point.

`procrastinate.contrib.django.AUTODISCOVER_MODULE_NAME = "tasks"` — Procrastinate
scans each INSTALLED_APP for a top-level `tasks` module and imports it so
`@app.task` decorators register. The real task lives in
`session_service/tasks.py`; this file just imports it to wire up discovery.
"""

from agent_on_demand.session_service.tasks import execute_turn

__all__ = ["execute_turn"]
