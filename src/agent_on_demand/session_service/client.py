import logging

from agent_on_demand.models import UserSpritesKey

from .backend import BackendClient, BackendError
from .errors import NoBackendCredentialsError
from .registry import get_backend

logger = logging.getLogger(__name__)


def get_client(user, backend: str = "sprites") -> BackendClient | None:
    """Build a backend client from the caller's stored token.

    Returns None when the user has no token configured. The backend
    discriminator selects which `Backend` implementation in the registry
    creates the per-user client.
    """
    # Until PR 8 generalizes `UserSpritesKey` → `UserBackendCredential`
    # keyed on (user, backend), only the sprites backend has a stored
    # credential model. Reject other backends explicitly so the boundary
    # fails fast at the credential layer instead of silently passing a
    # Sprites token to (e.g.) a Modal client and producing a confusing
    # downstream auth error.
    if backend != "sprites":
        raise NoBackendCredentialsError(
            f"Backend {backend!r} has no credential model yet "
            "(blocked until PR 8 generalizes UserSpritesKey → UserBackendCredential)"
        )
    try:
        token = user.sprites_key.get_api_key()
    except UserSpritesKey.DoesNotExist:
        return None
    return get_backend(backend).create_client(token)


def require_client(user, backend: str = "sprites") -> BackendClient:
    client = get_client(user, backend)
    if client is None:
        raise NoBackendCredentialsError("No backend credentials configured")
    return client


def best_effort_delete(client: BackendClient, name: str) -> None:
    try:
        client.destroy(name)
    except BackendError:
        logger.warning("Failed to cleanup session %s", name, exc_info=True)
