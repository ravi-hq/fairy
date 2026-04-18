import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from sprites import NetworkPolicy, PolicyRule, SpriteError

from fairy.models import (
    Agent,
    APIKey,
    Environment,
    EnvironmentVersion,
    UserRuntimeKey,
    UserSpritesKey,
)


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def api_key(user):
    instance, raw_key = APIKey.create_key(user, "test-key")
    return instance, raw_key


@pytest.fixture
def auth_headers(api_key):
    _, raw_key = api_key
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


@pytest.fixture
def sprites_key(user):
    usk = UserSpritesKey(user=user)
    usk.set_api_key("fake-sprites-token")
    usk.save()
    return usk


@pytest.fixture
def runtime_key(user, sprites_key):
    urk = UserRuntimeKey(user=user, runtime="claude")
    urk.set_api_key("fake-anthropic-key")
    urk.save()
    return urk


@pytest.fixture
def agent(user):
    return Agent.objects.create(
        user=user,
        name="Test Agent",
        model="claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )


@pytest.fixture
def mock_sprites(mocker):
    mock_sprite = mocker.MagicMock()
    mock_fs = mocker.MagicMock()
    mock_sprite.filesystem.return_value = mock_fs
    mock_fs.__truediv__ = mocker.Mock(return_value=mock_fs)
    mock_fs.write_text = mocker.Mock()
    mock_sprite.command.return_value.run = mocker.Mock()

    mock_client = mocker.MagicMock()
    mock_client.create_sprite.return_value = mock_sprite
    mocker.patch("fairy.views._get_client", return_value=mock_client)
    mocker.patch("fairy.views.threading.Thread")
    return mock_client, mock_sprite


def _make_env(user, *, networking_type="unrestricted", allowed_hosts=None):
    networking_config = {"allowed_hosts": allowed_hosts} if allowed_hosts is not None else {}
    env = Environment.objects.create(
        user=user,
        name=f"env-{networking_type}",
        networking_type=networking_type,
        networking_config=networking_config,
        version=1,
    )
    EnvironmentVersion.objects.create(
        environment=env,
        version=1,
        name=env.name,
        packages=env.packages,
        env_vars=env.env_vars,
        setup_script=env.setup_script,
        networking_type=env.networking_type,
        networking_config=env.networking_config,
    )
    return env


class TestSessionNetworkingIntegration:
    def test_limited_networking_applies_policy_to_sprite(
        self, client: Client, auth_headers, runtime_key, user, agent, mock_sprites
    ):
        _, mock_sprite = mock_sprites
        env = _make_env(
            user, networking_type="limited", allowed_hosts=["api.anthropic.com", "*.github.com"]
        )

        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202

        assert mock_sprite.update_network_policy.call_count == 1
        policy = mock_sprite.update_network_policy.call_args[0][0]
        assert isinstance(policy, NetworkPolicy)
        assert policy.rules == [
            PolicyRule(domain="api.anthropic.com", action="allow"),
            PolicyRule(domain="*.github.com", action="allow"),
            PolicyRule(domain="*", action="deny"),
        ]

    def test_unrestricted_networking_skips_policy_call(
        self, client: Client, auth_headers, runtime_key, user, agent, mock_sprites
    ):
        _, mock_sprite = mock_sprites
        env = _make_env(user, networking_type="unrestricted")

        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        assert mock_sprite.update_network_policy.call_count == 0

    def test_session_without_environment_skips_policy_call(
        self, client: Client, auth_headers, runtime_key, agent, mock_sprites
    ):
        _, mock_sprite = mock_sprites

        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        assert mock_sprite.update_network_policy.call_count == 0

    def test_policy_apply_failure_cleans_up_sprite(
        self, client: Client, auth_headers, runtime_key, user, agent, mock_sprites
    ):
        mock_client, mock_sprite = mock_sprites
        mock_sprite.update_network_policy.side_effect = SpriteError("policy rejected")
        env = _make_env(user, networking_type="limited", allowed_hosts=["api.anthropic.com"])

        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 502
        assert "Failed to prepare Sprite" in resp.json()["detail"]
        assert mock_client.delete_sprite.called

    def test_limited_with_empty_allowed_hosts_denies_all(
        self, client: Client, auth_headers, runtime_key, user, agent, mock_sprites
    ):
        _, mock_sprite = mock_sprites
        env = _make_env(user, networking_type="limited", allowed_hosts=[])

        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202

        assert mock_sprite.update_network_policy.call_count == 1
        policy = mock_sprite.update_network_policy.call_args[0][0]
        assert policy.rules == [PolicyRule(domain="*", action="deny")]
