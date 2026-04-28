"""Drop the legacy ``UserSpritesKey`` table.

Step 2 of the credential generalization started in #256 (migration
``0019_user_backend_credential``). That PR created
``UserBackendCredential``, backfilled every ``UserSpritesKey`` row into
it as ``backend="sprites"``, and left the legacy table in place behind a
``_lookup_token`` fallback so no caller lost access during the deploy
window. The backfill has now soaked in production, so this forward-only
migration removes the legacy table and the application-side fallback
that read from it.

The conditional ``IgnoreMigration`` opts this migration out of
django-migration-linter, which flags the underlying ``DROP TABLE`` —
correctly identifying it as destructive. Drops are documented as
forward-only in this repo (see ``CLAUDE.md`` danger zones); this
particular drop is intentional and is reviewed at the PR level rather
than at the linter level. The import is guarded because
django-migration-linter is a dev-only extra and is absent in production
(see ``settings.py``).
"""

import importlib.util

from django.db import migrations


_extra_operations = []
if importlib.util.find_spec("django_migration_linter") is not None:
    from django_migration_linter import IgnoreMigration

    _extra_operations.append(IgnoreMigration())


class Migration(migrations.Migration):
    dependencies = [
        ("fairy", "0020_drop_sprite_name"),
    ]

    operations = [
        *_extra_operations,
        migrations.DeleteModel(name="UserSpritesKey"),
    ]
