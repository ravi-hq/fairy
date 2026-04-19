from django.http import HttpRequest, JsonResponse
from django.utils import timezone as tz
from ninja import NinjaAPI
from ninja.errors import HttpError, ValidationError
from ninja.security import HttpBearer

from agent_on_demand.models import APIKey


class ApiKeyAuth(HttpBearer):
    """Bearer-token auth mirroring the old require_api_key decorator.

    Sets request.user and request.api_key_obj for downstream handlers.
    Raises HttpError with specific messages so clients can distinguish
    invalid vs. inactive vs. expired keys.
    """

    def authenticate(self, request: HttpRequest, token: str):
        key_hash = APIKey.hash_key(token)
        try:
            api_key = APIKey.objects.select_related("user").get(key_hash=key_hash)
        except APIKey.DoesNotExist:
            raise HttpError(401, "Invalid API key")
        if not api_key.is_active:
            raise HttpError(401, "API key is inactive")
        if api_key.expires_at and api_key.expires_at <= tz.now():
            raise HttpError(401, "API key has expired")
        request.user = api_key.user
        request.api_key_obj = api_key
        return api_key.user


api = NinjaAPI(
    title="Agent on Demand",
    urls_namespace="aod",
    auth=ApiKeyAuth(),
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@api.exception_handler(ValidationError)
def _validation_error(request, exc: ValidationError):
    return JsonResponse({"detail": exc.errors}, status=422)


@api.exception_handler(HttpError)
def _http_error(request, exc: HttpError):
    # Normalize Ninja's default body-parse message to the old shape.
    message = str(exc)
    if exc.status_code == 400 and message.startswith("Cannot parse request body"):
        message = "Invalid JSON"
    return JsonResponse({"detail": message}, status=exc.status_code)
