"""Rewrite: migrate Agent.model strings to provider/model_id, convert
UserRuntimeKey rows to UserCredential rows, fold claude-oauth runtime into
claude, drop choices from Agent.model/runtime, and drop the
UserRuntimeKey table.

Data migration runs first so the old rows are still available to read.
"""

from django.db import migrations, models


MODEL_RENAMES = {
    # anthropic
    "claude-opus-4-6": "anthropic/claude-opus-4-6",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4-6",
    "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
    "claude-opus-4-0-20250514": "anthropic/claude-opus-4-0-20250514",
    "claude-sonnet-4-0-20250514": "anthropic/claude-sonnet-4-0-20250514",
    "claude-sonnet-4-5-20250514": "anthropic/claude-sonnet-4-5-20250514",
    "claude-3-5-haiku-20241022": "anthropic/claude-3-5-haiku-20241022",
    # openai
    "gpt-4.1": "openai/gpt-4.1",
    "o3": "openai/o3",
    "o4-mini": "openai/o4-mini",
    # google
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
}

RUNTIME_TO_PROVIDER_KIND = {
    "claude": "provider:anthropic",
    "codex": "provider:openai",
    "gemini": "provider:google",
    "claude-oauth": "runtime_token:claude-oauth",
}


def migrate_data(apps, schema_editor):
    Agent = apps.get_model("fairy", "Agent")
    AgentVersion = apps.get_model("fairy", "AgentVersion")
    UserRuntimeKey = apps.get_model("fairy", "UserRuntimeKey")
    UserCredential = apps.get_model("fairy", "UserCredential")

    for agent in Agent.objects.all():
        changed_fields = []
        if agent.model in MODEL_RENAMES:
            agent.model = MODEL_RENAMES[agent.model]
            changed_fields.append("model")
        if agent.runtime == "claude-oauth":
            agent.runtime = "claude"
            changed_fields.append("runtime")
        if changed_fields:
            agent.save(update_fields=changed_fields)

    for av in AgentVersion.objects.all():
        changed_fields = []
        if av.model in MODEL_RENAMES:
            av.model = MODEL_RENAMES[av.model]
            changed_fields.append("model")
        if av.runtime == "claude-oauth":
            av.runtime = "claude"
            changed_fields.append("runtime")
        if changed_fields:
            av.save(update_fields=changed_fields)

    for urk in UserRuntimeKey.objects.all():
        kind = RUNTIME_TO_PROVIDER_KIND.get(urk.runtime)
        if kind is None:
            continue
        UserCredential.objects.update_or_create(
            user=urk.user,
            kind=kind,
            defaults={"value_encrypted": urk.encrypted_key},
        )


def unmigrate_data(apps, schema_editor):
    # Best-effort reverse: recreate UserRuntimeKey rows from UserCredential.
    UserRuntimeKey = apps.get_model("fairy", "UserRuntimeKey")
    UserCredential = apps.get_model("fairy", "UserCredential")
    kind_to_runtime = {v: k for k, v in RUNTIME_TO_PROVIDER_KIND.items()}
    for cred in UserCredential.objects.all():
        runtime = kind_to_runtime.get(cred.kind)
        if runtime is None:
            continue
        UserRuntimeKey.objects.update_or_create(
            user=cred.user,
            runtime=runtime,
            defaults={"encrypted_key": cred.value_encrypted},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("fairy", "0015_usercredential"),
    ]

    operations = [
        migrations.RunPython(migrate_data, reverse_code=unmigrate_data),
        migrations.AlterField(
            model_name="agent",
            name="model",
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name="agent",
            name="runtime",
            field=models.CharField(max_length=32),
        ),
        migrations.DeleteModel(name="UserRuntimeKey"),
    ]
