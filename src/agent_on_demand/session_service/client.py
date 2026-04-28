import logging
from functools import cache

from agent_on_demand.models import UserSpritesKey

from .backend import Backend, BackendClient, BackendError
from .errors import NoSpritesKeyError
from .sprites_backend import SpritesBackend

logger = logging.getLogger(__name__)


@cache
def _backend() -> Backend:
    """Lazily construct the backend singleton.

    `SpritesBackend.__init__` applies a websocket close-timeout monkeypatch
    on first instantiation, so we defer construction until the first
    `get_client` call rather than running at import time.
    """
    return SpritesBackend()


def get_client(user) -> BackendClient | None:
    """Build a backend client from the caller's stored token.

    Returns None when the user has no token configured.
    """
    try:
        token = user.sprites_key.get_api_key()
    except UserSpritesKey.DoesNotExist:
        return None
    return _backend().create_client(token)


def require_client(user) -> BackendClient:
    client = get_client(user)
    if client is None:
        raise NoSpritesKeyError("No Sprites API key configured")
    return client


def best_effort_delete(client: BackendClient, name: str) -> None:
    try:
        client.destroy(name)
    except BackendError:
        logger.warning("Failed to cleanup session %s", name, exc_info=True)
