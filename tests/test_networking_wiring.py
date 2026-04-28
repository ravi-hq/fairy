import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from sprites import SpriteError

from agent_on_demand.session_service.backends.base import NetworkPolicy, PolicyRule

from agent_on_demand.models import (
    Agent,
    APIKey,
    Environment,
    EnvironmentVersion,
    UserBackendCredential,
    UserCredential,
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
    cred = UserBackendCredential(user=user, backend="sprites")
    cred.set_token("fake-sprites-token")
    cred.save()
    return cred


@pytest.fixture
def runtime_key(user, sprites_key):
    cred = UserCredential(user=user, kind="provider:anthropic")
    cred.set_value("fake-anthropic-key")
    cred.save()
    return cred


@pytest.fixture
def agent(user):
    return Agent.objects.create(
        user=user,
        name="Test Agent",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )


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
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
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

        sprite = fake_sprites.last_sprite()
        assert len(sprite.policies) == 1
        policy = sprite.policies[0]
        assert isinstance(policy, NetworkPolicy)
        assert policy.rules == (
            PolicyRule(domain="api.anthropic.com", action="allow"),
            PolicyRule(domain="*.github.com", action="allow"),
            PolicyRule(domain="*", action="deny"),
        )

    def test_unrestricted_networking_skips_policy_call(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
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
        assert fake_sprites.last_sprite().policies == []

    def test_session_without_environment_skips_policy_call(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        assert fake_sprites.last_sprite().policies == []

    def test_policy_apply_failure_cleans_up_sprite(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites, mocker
    ):
        """update_network_policy failing mid-provision tears the Sprite back
        down and marks the session failed. The HTTP response is still 202
        because provisioning now runs on the worker — the failure surfaces
        through session.status, not the create response."""
        from agent_on_demand.models import AgentSession

        env = _make_env(user, networking_type="limited", allowed_hosts=["api.anthropic.com"])

        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.raise_on("update_network_policy", SpriteError("policy rejected"))
            return sprite

        mocker.patch.object(fake_sprites, "create_sprite", side_effect=wrapped)

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

        session = AgentSession.objects.get(pk=resp.json()["id"])
        assert session.status == "failed"
        assert fake_sprites.deleted  # provision_session tore the Sprite back down

    def test_limited_with_empty_allowed_hosts_denies_all(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
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

        sprite = fake_sprites.last_sprite()
        assert len(sprite.policies) == 1
        assert sprite.policies[0].rules == (PolicyRule(domain="*", action="deny"),)
