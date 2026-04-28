import json
from typing import TypeVar

from django.http import HttpRequest, JsonResponse
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


def parse_request_body(
    request: HttpRequest, schema: type[T]
) -> tuple[T | None, JsonResponse | None]:
    """Decode JSON body and validate against a pydantic schema.

    Error response shapes are part of the public API contract — 400 for invalid
    JSON and 422 for schema violations, both with the documented `detail` shape.
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({"detail": "Invalid JSON"}, status=400)

    if not isinstance(body, dict):
        # `schema(**body)` would raise TypeError on lists/scalars and surface
        # as a 500. Treat any non-object top-level JSON as the same client
        # error shape as a parse failure.
        return None, JsonResponse({"detail": "Invalid JSON"}, status=400)

    try:
        return schema(**body), None
    except ValidationError as e:
        return None, JsonResponse({"detail": e.errors(include_context=False)}, status=422)
