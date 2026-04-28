"""Add `AgentSession.backend` discriminator column.

Forward-only, additive migration. The default value ``"sprites"`` covers
existing rows so no data backfill is required. The column is not yet
exposed via the API surface; it routes through the in-process
``BACKENDS`` registry in ``session_service/registry.py``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fairy", "0016_runtime_redesign"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentsession",
            name="backend",
            field=models.CharField(default="sprites", max_length=32),
        ),
    ]
