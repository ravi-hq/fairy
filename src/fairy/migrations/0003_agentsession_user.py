import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def set_default_user(apps, schema_editor):
    AgentSession = apps.get_model("fairy", "AgentSession")
    AgentSession.objects.filter(user__isnull=True).update(user_id=1)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("fairy", "0002_agentsession_alter_userruntimekey_runtime_and_more"),
    ]

    operations = [
        # Step 1: Add nullable FK
        migrations.AddField(
            model_name="agentsession",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="agent_sessions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Step 2: Backfill existing rows to user_id=1
        migrations.RunPython(set_default_user, migrations.RunPython.noop),
        # Step 3: Make non-nullable
        migrations.AlterField(
            model_name="agentsession",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="agent_sessions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
