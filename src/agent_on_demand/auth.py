import functools
import inspect

from django.http import JsonResponse
from django.utils import timezone as tz

from agent_on_demand.models import APIKey


def _check_api_key_sync(request):
    """Return (api_key, error_response) — error_response is None on success."""
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None, JsonResponse({"detail": "Missing or invalid Authorization header"}, status=401)
    raw_key = auth_header[7:]
    key_hash = APIKey.hash_key(raw_key)
    try:
        api_key = APIKey.objects.select_related("user").get(key_hash=key_hash)
    except APIKey.DoesNotExist:
        return None, JsonResponse({"detail": "Invalid API key"}, status=401)
    if not api_key.is_active:
        return None, JsonResponse({"detail": "API key is inactive"}, status=401)
    if api_key.expires_at and api_key.expires_at <= tz.now():
        return None, JsonResponse({"detail": "API key has expired"}, status=401)
    return api_key, None


async def _check_api_key_async(request):
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None, JsonResponse({"detail": "Missing or invalid Authorization header"}, status=401)
    raw_key = auth_header[7:]
    key_hash = APIKey.hash_key(raw_key)
    try:
        api_key = await APIKey.objects.select_related("user").aget(key_hash=key_hash)
    except APIKey.DoesNotExist:
        return None, JsonResponse({"detail": "Invalid API key"}, status=401)
    if not api_key.is_active:
        return None, JsonResponse({"detail": "API key is inactive"}, status=401)
    if api_key.expires_at and api_key.expires_at <= tz.now():
        return None, JsonResponse({"detail": "API key has expired"}, status=401)
    return api_key, None


def require_api_key(view_func):
    """Authenticate requests via Bearer token. Dispatches sync or async based on view type."""
    if inspect.iscoroutinefunction(view_func):

        @functools.wraps(view_func)
        async def async_wrapper(request, *args, **kwargs):
            api_key, err = await _check_api_key_async(request)
            if err is not None:
                return err
            request.user = api_key.user
            request.api_key_obj = api_key
            return await view_func(request, *args, **kwargs)

        return async_wrapper

    @functools.wraps(view_func)
    def sync_wrapper(request, *args, **kwargs):
        api_key, err = _check_api_key_sync(request)
        if err is not None:
            return err
        request.user = api_key.user
        request.api_key_obj = api_key
        return view_func(request, *args, **kwargs)

    return sync_wrapper
