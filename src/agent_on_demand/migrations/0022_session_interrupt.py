"""Add interrupt support to sessions.

Adds:

- ``AgentSession.interrupt_requested`` (BooleanField, default=False) — the
  transient request flag set by ``POST /sessions/{id}/interrupt`` and
  cleared by the worker once it finalizes the turn.
- ``SessionTurn.status="interrupted"`` choice — a new terminal turn
  status surfaced when a turn was killed at user request, distinct from
  ``failed``.

The ``IgnoreMigration`` opt-out covers the new ``NOT NULL`` column. The
migration linter flags every new ``NOT NULL`` field, but a BooleanField
with ``default=False`` is safe under PostgreSQL >= 11 — the column is
added in O(1) without a table rewrite, and existing rows pick up the
default. Reviewed at the PR level instead of the linter level. The
import guard matches the pattern in ``0021_drop_user_sprites_key`` —
``django-migration-linter`` is a dev-only extra and is absent in
production (see ``settings.py``).
"""

import importlib.util

from django.db import migrations, models


_extra_operations = []
if importlib.util.find_spec("django_migration_linter") is not None:
    from django_migration_linter import IgnoreMigration

    _extra_operations.append(IgnoreMigration())


class Migration(migrations.Migration):

    dependencies = [
        ("fairy", "0021_drop_user_sprites_key"),
    ]

    operations = [
        *_extra_operations,
        migrations.AddField(
            model_name="agentsession",
            name="interrupt_requested",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="sessionturn",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("interrupted", "Interrupted"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
    ]
