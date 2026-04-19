from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fairy", "0012_add_runtime_session_id"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="agentsession",
            index=models.Index(
                fields=["status", "updated_at"],
                name="agentsession_status_upd_idx",
            ),
        ),
    ]
