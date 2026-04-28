"""Add `AgentSession.backend` discriminator column.

Forward-only, additive migration. The default value ``"sprites"`` covers
existing rows so no data backfill is required. The column is not yet
exposed via the API surface; it routes through the in-process
``BACKENDS`` registry in ``session_service/registry.py``.

The conditional ``IgnoreMigration`` opts this single migration out of
the django-migration-linter checks. The SqliteAnalyser's ``NOT_NULL``
rule fires on any ``AddField`` because Django's SQLite schema editor
emits a full table rebuild (``CREATE TABLE new__foo (..., backend NOT
NULL, ...)``) — even though production Postgres emits the safe ``ADD
COLUMN backend ... DEFAULT 'sprites' NOT NULL``. Scoping the opt-out to
this migration (rather than globally suppressing ``NOT_NULL`` in
``MIGRATION_LINTER_OPTIONS``) keeps the linter strict for every other
migration. The import is guarded because django-migration-linter is a
dev-only extra and is absent in production (see ``settings.py``).
"""

import importlib.util

from django.db import migrations, models


_extra_operations = []
if importlib.util.find_spec("django_migration_linter") is not None:
    from django_migration_linter import IgnoreMigration

    _extra_operations.append(IgnoreMigration())


class Migration(migrations.Migration):
    dependencies = [
        ("fairy", "0016_runtime_redesign"),
    ]

    operations = [
        *_extra_operations,
        migrations.AddField(
            model_name="agentsession",
            name="backend",
            field=models.CharField(default="sprites", max_length=32),
        ),
    ]
