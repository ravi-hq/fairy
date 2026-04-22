from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

from .errors import raise_for_status
from .models import StreamEvent

if TYPE_CHECKING:
    import httpx


def _parse_data_line(line: str) -> StreamEvent | None:
    if not line.startswith("data:"):
        return None
    raw = line[5:].lstrip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return StreamEvent.from_payload(payload)


def iter_sse(response: httpx.Response) -> Iterator[StreamEvent]:
    """Yield parsed StreamEvents from a sync httpx streaming response.

    The caller is responsible for the surrounding `with client.stream(...)`
    context; we just walk the body lines.
    """
    if response.status_code >= 400:
        body = response.read()
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = body.decode(errors="replace") if body else None
        raise_for_status(
            response.status_code, parsed, response.request.method, str(response.request.url)
        )
    for line in response.iter_lines():
        event = _parse_data_line(line)
        if event is not None:
            yield event


async def aiter_sse(response: httpx.Response) -> AsyncIterator[StreamEvent]:
    if response.status_code >= 400:
        body = await response.aread()
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = body.decode(errors="replace") if body else None
        raise_for_status(
            response.status_code, parsed, response.request.method, str(response.request.url)
        )
    async for line in response.aiter_lines():
        event = _parse_data_line(line)
        if event is not None:
            yield event
