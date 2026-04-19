from django.conf import settings
from django.http import JsonResponse

from sprites import SpritesClient

from agent_on_demand.models import UserRuntimeKey, UserSpritesKey


def _get_sprites_key(user) -> str | None:
    """Look up the user's stored Sprites API token."""
    try:
        return user.sprites_key.get_api_key()
    except UserSpritesKey.DoesNotExist:
        return None


def _get_client(user) -> SpritesClient | None:
    """Build a SpritesClient using the caller's per-user token."""
    token = _get_sprites_key(user)
    if token is None:
        return None
    return SpritesClient(token=token, base_url=settings.SPRITES_BASE_URL)


def _no_sprites_key_response() -> JsonResponse:
    return JsonResponse({"detail": "No Sprites API key configured"}, status=400)


def _get_runtime_key(user, runtime: str) -> str | None:
    """Look up the user's stored API key for a runtime."""
    try:
        urk = UserRuntimeKey.objects.get(user=user, runtime=runtime)
        return urk.get_api_key()
    except UserRuntimeKey.DoesNotExist:
        return None
