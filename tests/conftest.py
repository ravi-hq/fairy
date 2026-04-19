import pytest
from django.test import Client

from tests.fakes.sprite import RecordingSpritesClient


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def fake_sprites(mocker):
    """Swap the real SpritesClient for a recording fake and stub the
    background-execution thread so create_session calls don't spawn work.

    Returns the RecordingSpritesClient; use `.last_sprite()` or `.sprites`
    to reach the RecordingSprite(s) created by the test under inspection.
    """
    fake = RecordingSpritesClient()
    mocker.patch("agent_on_demand.session_service.client.get_client", return_value=fake)
    mocker.patch("agent_on_demand.session_service.get_client", return_value=fake)
    mocker.patch("agent_on_demand.session_service.turn.threading.Thread")
    return fake
