import functools
from datetime import timezone

from django.http import JsonResponse
from django.utils import timezone as tz

from fairy.models import APIKey


def require_api_key(view_func):
    """Decorator that authenticates requests via Bearer token.

    Looks up the API key by hash, checks active/expiry, and sets
    request.user and request.api_key_obj.
    """

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return JsonResponse(
                {"detail": "Missing or invalid Authorization header"},
                status=401,
            )

        raw_key = auth_header[7:]  # strip "Bearer "
        key_hash = APIKey.hash_key(raw_key)

        try:
            api_key = APIKey.objects.select_related("user").get(key_hash=key_hash)
        except APIKey.DoesNotExist:
            return JsonResponse({"detail": "Invalid API key"}, status=401)

        if not api_key.is_active:
            return JsonResponse({"detail": "API key is inactive"}, status=401)

        if api_key.expires_at and api_key.expires_at <= tz.now():
            return JsonResponse({"detail": "API key has expired"}, status=401)

        request.user = api_key.user
        request.api_key_obj = api_key
        return view_func(request, *args, **kwargs)

    return wrapper
