from agent_on_demand.models import UserRuntimeKey


def _get_runtime_key(user, runtime: str) -> str | None:
    """Look up the user's stored API key for a runtime."""
    try:
        urk = UserRuntimeKey.objects.get(user=user, runtime=runtime)
        return urk.get_api_key()
    except UserRuntimeKey.DoesNotExist:
        return None
