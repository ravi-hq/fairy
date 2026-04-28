import pytest
from django.test import Client

from tests.fakes.sprite import RecordingSpritesClient


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def fake_sprites(mocker):
    """Swap the real backend client for a recording fake, run the provision
    task inline, and stub the per-turn task enqueue.

    Returns the RecordingSpritesClient; use `.last_sprite()` or `.sprites`
    to reach the RecordingSprite(s) created by the test under inspection.
    The fake satisfies both the legacy sprites SDK shape and the
    `BackendClient` Protocol so existing tests (which patch
    `delete_sprite` etc.) keep working alongside production callers that
    now go through the Protocol.

    `provision_session_task.defer` is patched to execute the task body
    synchronously against the fake client. That keeps HTTP-layer tests
    able to assert on the Sprite writes/commands that provisioning made —
    even though in production they'd happen on the worker. The downstream
    `execute_turn.defer` that the provision task would otherwise chain
    into is stubbed so the turn itself stays unrun.
    """
    fake = RecordingSpritesClient()
    mocker.patch("agent_on_demand.session_service.client.get_client", return_value=fake)
    mocker.patch("agent_on_demand.session_service.get_client", return_value=fake)

    mocker.patch("agent_on_demand.session_service.tasks.execute_turn.defer")

    from agent_on_demand.session_service.tasks import (
        destroy_session_task,
        provision_session_task,
    )

    def _inline_provision(**kwargs):
        provision_session_task(**kwargs)

    def _inline_destroy(**kwargs):
        destroy_session_task(**kwargs)

    mocker.patch.object(provision_session_task, "defer", side_effect=_inline_provision)
    mocker.patch.object(destroy_session_task, "defer", side_effect=_inline_destroy)
    return fake


@pytest.fixture
def fake_backend(mocker):
    """Backend-Protocol-only fake for tests that exercise the new
    `BackendClient` / `SessionHandle` shape directly.

    Returns the `RecordingBackendClient` patched in for `get_client`.
    `tasks.py` still calls sprites-specific methods on its sprite handle
    (the threading core moves to the Protocol in PR 4 of the
    session-backend extraction), so this fixture intentionally does NOT
    inline-run `provision_session_task` / `destroy_session_task` — use
    `fake_sprites` for tests that drive the full task path. Use
    `fake_backend` for tests that call `provision_session` /
    `destroy_session` directly and want pure-Protocol assertions.
    """
    from tests.fakes.backend import RecordingBackendClient

    fake = RecordingBackendClient()
    mocker.patch("agent_on_demand.session_service.client.get_client", return_value=fake)
    mocker.patch("agent_on_demand.session_service.get_client", return_value=fake)
    return fake
