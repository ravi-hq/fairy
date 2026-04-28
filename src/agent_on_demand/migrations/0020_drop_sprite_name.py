"""Step 2 of two-step rename: drop ``AgentSession.sprite_name``.

PR #258 added ``backend_handle`` and dual-wrote both columns with a
fallback read of ``backend_handle or sprite_name``. Once that deploy has
soaked for at least one day (longer than any expected session lifetime),
every live session has ``backend_handle`` populated and ``sprite_name``
is dead weight. This migration removes the column for good.

Forward-only. Rolling this back would resurrect an empty column —
sessions provisioned after this deploy never wrote ``sprite_name`` and
the legacy fallback would strand them. Re-adding the column is a manual
DB fix, not a Django migration rollback.

The conditional ``IgnoreMigration`` opts this single migration out of
the django-migration-linter's ``DROP_COLUMN`` rule. The mechanical
safety the linter provides is appropriate for accidental drops; this
drop is deliberate, the dual-write soak is the actual deploy-time
safety, and CLAUDE.md's danger-zone policy gates the change behind
human review. The import is guarded because django-migration-linter is
a dev-only extra and is absent in production (see ``settings.py``);
PR #259 hot-fixed migration 0018 from bare-import to this pattern.
"""

import importlib.util

from django.db import migrations


_extra_operations = []
if importlib.util.find_spec("django_migration_linter") is not None:
    from django_migration_linter import IgnoreMigration

    _extra_operations.append(IgnoreMigration())


class Migration(migrations.Migration):
    dependencies = [
        ("fairy", "0019_user_backend_credential"),
    ]

    operations = [
        *_extra_operations,
        migrations.RemoveField(
            model_name="agentsession",
            name="sprite_name",
        ),
    ]
