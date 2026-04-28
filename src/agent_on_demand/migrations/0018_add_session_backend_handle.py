"""Step 1 of two-step rename: add ``backend_handle`` alongside ``sprite_name``.

Application code dual-writes both columns and reads ``backend_handle`` with a
fallback to ``sprite_name`` (for sessions provisioned during the deploy window
before the dual-write code shipped). A follow-up forward-only migration drops
``sprite_name`` after this dual-write deploy soaks for at least one day.

The new field is ``null=True`` *and* ``default=""`` — the default backfills
existing rows safely on Postgres; ``null=True`` is the conservative pairing.
Application code always writes a string value, so reads are typed-string.

``IgnoreMigration`` is the documented escape hatch for false positives in
the SQL analyser. CI runs the linter against SQLite (matching local test
DB), and SQLite's table-rebuild dance for AddField triggers ``NOT NULL
constraint on columns`` on every existing NOT NULL column in the table — a
known limitation of the analyser that cannot be turned off per-rule. The
column add itself is genuinely safe (nullable, defaulted); review-gate
this migration manually per CLAUDE.md's danger-zone policy.
"""

from django.db import migrations, models
from django_migration_linter import IgnoreMigration


class Migration(migrations.Migration):
    dependencies = [
        ("fairy", "0017_add_session_backend"),
    ]

    operations = [
        IgnoreMigration(),
        migrations.AddField(
            model_name="agentsession",
            name="backend_handle",
            field=models.CharField(blank=True, default="", max_length=100, null=True),
        ),
    ]
