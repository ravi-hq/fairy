import pytest
from django.test import Client

from tests.fakes.sprite import RecordingSpritesClient


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def fake_sprites(mocker):
    """Swap the real SpritesClient for a recording fake, run the provision
    task inline, and stub the per-turn task enqueue.

    Returns the RecordingSpritesClient; use `.last_sprite()` or `.sprites`
    to reach the RecordingSprite(s) created by the test under inspection.

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
    mocker.patch("agent_on_demand.session_service.turn.execute_turn.defer")

    from agent_on_demand.session_service.tasks import provision_session_task

    def _inline_defer(**kwargs):
        provision_session_task(**kwargs)

    mocker.patch.object(provision_session_task, "defer", side_effect=_inline_defer)
    return fake
