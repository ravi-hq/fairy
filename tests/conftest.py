import pytest
from django.test import Client

from tests.fakes.sprite import RecordingSpritesClient


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def fake_sprites(mocker):
    """Swap the real SpritesClient for a recording fake and stub the
    Procrastinate task enqueue so create_session doesn't hit the real
    broker.

    Returns the RecordingSpritesClient; use `.last_sprite()` or `.sprites`
    to reach the RecordingSprite(s) created by the test under inspection.
    """
    fake = RecordingSpritesClient()
    mocker.patch("agent_on_demand.session_service.client.get_client", return_value=fake)
    mocker.patch("agent_on_demand.session_service.get_client", return_value=fake)
    # Stub task enqueue — tests that care about the enqueue can spy on this
    # directly via `mocker.spy(execute_turn, "defer")` in their body.
    mocker.patch("agent_on_demand.session_service.turn.execute_turn.defer")
    return fake
