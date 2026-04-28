import logging

from agent_on_demand.models import UserBackendCredential

from .backends import BackendClient, BackendError, get_backend
from .errors import NoBackendCredentialsError

logger = logging.getLogger(__name__)


def _lookup_token(user, backend: str) -> str | None:
    try:
        cred = UserBackendCredential.objects.get(user=user, backend=backend)
    except UserBackendCredential.DoesNotExist:
        return None
    return cred.get_token()


def get_client(user, backend: str = "sprites") -> BackendClient | None:
    """Build a backend client from the caller's stored token.

    Returns None when the user has no token configured for `backend`.
    Raises `NoBackendCredentialsError` if `backend` is not a registered
    backend — that's a programmer error, not a credential gap.
    """
    try:
        impl = get_backend(backend)
    except KeyError as e:
        raise NoBackendCredentialsError(str(e)) from e
    token = _lookup_token(user, backend)
    if token is None:
        return None
    return impl.create_client(token)


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
