"""Generalize UserSpritesKey into UserBackendCredential.

Adds a per-backend credential table keyed on `(user, backend)` and backfills
every existing `UserSpritesKey` row as `backend="sprites"`. The ciphertext is
copied verbatim — `FIELD_ENCRYPTION_KEY` is unchanged, so the same Fernet key
decrypts both the legacy `encrypted_key` field and the new `encrypted_token`
field.

`UserSpritesKey` is intentionally left in place. A follow-up forward-only PR
drops it once the backfill has soaked.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_sprites_credentials(apps, schema_editor):
    UserSpritesKey = apps.get_model("fairy", "UserSpritesKey")
    UserBackendCredential = apps.get_model("fairy", "UserBackendCredential")
    for sprites_key in UserSpritesKey.objects.all():
        UserBackendCredential.objects.update_or_create(
            user=sprites_key.user,
            backend="sprites",
            defaults={"encrypted_token": sprites_key.encrypted_key},
        )


def drop_sprites_credentials(apps, schema_editor):
    UserBackendCredential = apps.get_model("fairy", "UserBackendCredential")
    UserBackendCredential.objects.filter(backend="sprites").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("fairy", "0018_add_session_backend_handle"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserBackendCredential",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("backend", models.CharField(max_length=32)),
                ("encrypted_token", models.BinaryField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="backend_credentials",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "user_backend_credentials",
                "constraints": [
                    models.UniqueConstraint(
                        fields=["user", "backend"], name="unique_user_backend_credential"
                    ),
                ],
            },
        ),
        migrations.RunPython(backfill_sprites_credentials, drop_sprites_credentials),
    ]
