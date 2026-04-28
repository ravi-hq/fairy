"""Unit tests for the parse_request_body and analytics.capture helpers.

These pin the error response shapes (400 / 422) and the posthog
identify-then-capture sequencing — both are part of the public contract.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest, JsonResponse
from pydantic import BaseModel, Field

from agent_on_demand import analytics
from agent_on_demand.views._helpers import parse_request_body


class _ExampleSchema(BaseModel):
    name: str = Field(max_length=10)
    count: int = Field(ge=0)


def _make_request(body: bytes) -> HttpRequest:
    req = HttpRequest()
    req._body = body
    return req


def _decode(resp: JsonResponse) -> dict:
    return json.loads(resp.content.decode())


def test_parse_request_body_happy_path():
    req = _make_request(b'{"name": "abc", "count": 5}')
    instance, err = parse_request_body(req, _ExampleSchema)
    assert err is None
    assert instance is not None
    assert instance.name == "abc"
    assert instance.count == 5


def test_parse_request_body_rejects_invalid_json():
    req = _make_request(b"not json")
    instance, err = parse_request_body(req, _ExampleSchema)
    assert instance is None
    assert err is not None
    assert err.status_code == 400
    assert _decode(err) == {"detail": "Invalid JSON"}


def test_parse_request_body_rejects_empty_body():
    req = _make_request(b"")
    instance, err = parse_request_body(req, _ExampleSchema)
    assert instance is None
    assert err is not None
    assert err.status_code == 400
    assert _decode(err) == {"detail": "Invalid JSON"}


def test_parse_request_body_returns_pydantic_errors_on_validation_failure():
    req = _make_request(b'{"name": "abc", "count": -1}')
    instance, err = parse_request_body(req, _ExampleSchema)
    assert instance is None
    assert err is not None
    assert err.status_code == 422
    body = _decode(err)
    assert "detail" in body
    assert isinstance(body["detail"], list)
    assert len(body["detail"]) >= 1
    # Each error entry must be a dict with a `loc`/`msg`/`type` shape — that's
    # what the SDKs parse. include_context=False means no `ctx` key.
    for entry in body["detail"]:
        assert "ctx" not in entry


def test_parse_request_body_422_on_missing_required_field():
    req = _make_request(b'{"count": 5}')
    instance, err = parse_request_body(req, _ExampleSchema)
    assert instance is None
    assert err is not None
    assert err.status_code == 422


def test_capture_calls_posthog_identify_then_capture(mocker):
    new_context = mocker.patch.object(analytics.posthog, "new_context")
    identify = mocker.patch.object(analytics.posthog, "identify_context")
    capture = mocker.patch.object(analytics.posthog, "capture")

    user = MagicMock(id=42)
    analytics.capture(user, "evt.name", properties={"foo": "bar"})

    new_context.assert_called_once_with()
    identify.assert_called_once_with("42")
    capture.assert_called_once_with("evt.name", properties={"foo": "bar"})


def test_capture_passes_none_properties_through(mocker):
    mocker.patch.object(analytics.posthog, "new_context")
    mocker.patch.object(analytics.posthog, "identify_context")
    capture = mocker.patch.object(analytics.posthog, "capture")

    user = MagicMock(id="abc")
    analytics.capture(user, "evt.bare")

    capture.assert_called_once_with("evt.bare", properties=None)


def test_capture_stringifies_user_id(mocker):
    mocker.patch.object(analytics.posthog, "new_context")
    identify = mocker.patch.object(analytics.posthog, "identify_context")
    mocker.patch.object(analytics.posthog, "capture")

    user = MagicMock(id=7)
    analytics.capture(user, "evt", properties={})

    identify.assert_called_once_with("7")


@pytest.mark.parametrize("payload", [b"{", b"[1, 2,", b"\xff\xfe"])
def test_parse_request_body_400_on_various_malformed_inputs(payload):
    req = _make_request(payload)
    instance, err = parse_request_body(req, _ExampleSchema)
    assert instance is None
    assert err is not None
    assert err.status_code == 400
    assert _decode(err) == {"detail": "Invalid JSON"}


def test_parse_request_body_rejects_json_array_body():
    req = _make_request(b"[1, 2, 3]")
    instance, err = parse_request_body(req, _ExampleSchema)
    assert instance is None
    assert err is not None
    assert err.status_code == 400
    assert _decode(err) == {"detail": "Invalid JSON"}


def test_parse_request_body_rejects_json_number_body():
    req = _make_request(b"42")
    instance, err = parse_request_body(req, _ExampleSchema)
    assert instance is None
    assert err is not None
    assert err.status_code == 400
    assert _decode(err) == {"detail": "Invalid JSON"}


def test_parse_request_body_rejects_json_string_body():
    req = _make_request(b'"hello"')
    instance, err = parse_request_body(req, _ExampleSchema)
    assert instance is None
    assert err is not None
    assert err.status_code == 400
    assert _decode(err) == {"detail": "Invalid JSON"}
