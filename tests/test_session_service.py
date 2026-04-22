"""Unit tests for the session_service package.

These tests call `provision_session(user, spec)` directly with a RecordingSprites
fake and assert on the recorded command/write sequence — complementing the
HTTP-layer integration tests that drive the same code path from the outside.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from sprites import SpriteError

from agent_on_demand.models import (
    AgentSession,
    AgentSessionLog,
    Environment,
    UserCredential,
    UserSpritesKey,
)
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service import (
    ProvisionError,
    SessionSpec,
    provision_session,
)


@pytest.fixture
def user(db):
    u = User.objects.create_user(username="svc", password="p")
    usk = UserSpritesKey(user=u)
    usk.set_api_key("fake-sprites-token")
    usk.save()
    cred = UserCredential(user=u, kind="provider:anthropic")
    cred.set_value("sk-xxx")
    cred.save()
    return u


def _spec(user, **overrides) -> SessionSpec:
    defaults = dict(
        name="sprite-x",
        runtime=RUNTIMES["claude"],
        model="anthropic/claude-sonnet-4-6",
        user=user,
        runtime_session_id="11111111-2222-3333-4444-555555555555",
        environment=None,
        repos=[],
        mcp_servers=[],
        skills=[],
    )
    defaults.update(overrides)
    return SessionSpec(**defaults)


class TestProvisionSessionOrder:
    def test_env_file_chmod_is_in_provision_script(self, user, fake_sprites):
        provision_session(user, _spec(user))
        sprite = fake_sprites.last_sprite()
        script = sprite.write_map()["/tmp/aod-provision.sh"]
        assert "chmod 600 /tmp/aod-env" in script

    def test_single_bash_command_invokes_provision_script(self, user, fake_sprites):
        provision_session(user, _spec(user))
        sprite = fake_sprites.last_sprite()
        # The whole provisioning phase uses exactly one sprite.command —
        # invoking the provision script. No per-stage chmod / clone commands.
        assert sprite.command_strings() == ["bash -l /tmp/aod-provision.sh"]

    def test_env_file_contains_credential_and_session_id(self, user, fake_sprites):
        provision_session(user, _spec(user))
        env_file = fake_sprites.last_sprite().write_map()["/tmp/aod-env"]
        # Credentials come from UserCredential rows for this user.
        assert "ANTHROPIC_API_KEY=sk-xxx" in env_file
        assert "AOD_SESSION_ID=11111111-2222-3333-4444-555555555555" in env_file
        # AOD_MODEL surfaces the canonical provider/model_id for meta-runtimes.
        assert "AOD_MODEL=anthropic/claude-sonnet-4-6" in env_file

    def test_env_file_includes_runtime_token_when_present(self, user, fake_sprites):
        oauth = UserCredential(user=user, kind="runtime_token:claude-oauth")
        oauth.set_value("oauth-token")
        oauth.save()
        provision_session(user, _spec(user))
        env_file = fake_sprites.last_sprite().write_map()["/tmp/aod-env"]
        assert "ANTHROPIC_API_KEY=sk-xxx" in env_file
        assert "CLAUDE_CODE_OAUTH_TOKEN=oauth-token" in env_file

    def test_env_vars_override_credentials(self, user, fake_sprites):
        """Environment.env_vars land last in the file, so when the same KEY
        appears in both a credential and an environment, the environment
        wins on `source` because later assignments overwrite earlier ones."""
        env = Environment.objects.create(
            user=user,
            name="override",
            env_vars={"ANTHROPIC_API_KEY": "env-override"},
            version=1,
        )
        provision_session(user, _spec(user, environment=env))
        body = fake_sprites.last_sprite().write_map()["/tmp/aod-env"]
        lines = body.strip().split("\n")
        cred_idx = next(
            i for i, line in enumerate(lines) if line.startswith("ANTHROPIC_API_KEY=sk-xxx")
        )
        override_idx = next(
            i for i, line in enumerate(lines) if line == "ANTHROPIC_API_KEY=env-override"
        )
        assert cred_idx < override_idx

    def test_no_run_agent_script_is_written(self, user, fake_sprites):
        """The per-turn runtime-CLI invocation is assembled inline in the
        worker task; nothing is written to /run-agent.sh."""
        provision_session(user, _spec(user))
        writes = fake_sprites.last_sprite().writes
        assert all(w.path != "/run-agent.sh" for w in writes)


class TestProvisionSessionFailureHandling:
    def test_create_sprite_failure_raises_provision_error_and_does_not_delete(
        self, user, fake_sprites
    ):
        fake_sprites.raise_on_create(SpriteError("boom"))
        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user))
        assert ei.value.stage == "create_sprite"
        assert fake_sprites.deleted == []

    def test_provision_script_failure_tags_stage_and_triggers_cleanup(
        self, user, fake_sprites, mocker
    ):
        env = Environment.objects.create(
            user=user,
            name="env",
            packages={"pip": ["pandas"]},
            version=1,
        )
        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.raise_on(lambda argv: argv[:2] == ("bash", "-l"), SpriteError("nope"))
            return sprite

        mocker.patch.object(fake_sprites, "create_sprite", side_effect=wrapped)

        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user, environment=env))
        assert ei.value.stage == "provision_setup"
        assert fake_sprites.deleted == ["sprite-x"]


class TestProvisionStageEvents:
    """Stage rows are written to AgentSessionLog so the SSE stream can surface
    provisioning progress. Unit tests here pass a real session id; skipped
    stages produce no rows, and a stage failure emits a `failed` event before
    the ProvisionError propagates."""

    def _make_session(self, user):
        return AgentSession.objects.create(
            user=user, runtime="claude", prompt="t", status="pending"
        )

    def test_minimal_spec_emits_expected_stages(self, user, fake_sprites):
        session = self._make_session(user)
        provision_session(user, _spec(user), session_id=str(session.id))
        events = list(
            AgentSessionLog.objects.filter(session=session, kind="stage")
            .order_by("id")
            .values("stage", "state")
        )
        # install_runtime, env_file, provision_setup, and runtime_config all
        # run unconditionally now. No tokens → git_credentials skipped. No
        # skills → skills skipped. Environment is None → network_policy skipped.
        assert events == [
            {"stage": "create_sprite", "state": "started"},
            {"stage": "create_sprite", "state": "done"},
            {"stage": "install_runtime", "state": "started"},
            {"stage": "install_runtime", "state": "done"},
            {"stage": "env_file", "state": "started"},
            {"stage": "env_file", "state": "done"},
            {"stage": "provision_setup", "state": "started"},
            {"stage": "provision_setup", "state": "done"},
            {"stage": "runtime_config", "state": "started"},
            {"stage": "runtime_config", "state": "done"},
        ]

    def test_done_events_carry_duration_ms(self, user, fake_sprites):
        session = self._make_session(user)
        provision_session(user, _spec(user), session_id=str(session.id))
        done = AgentSessionLog.objects.filter(session=session, kind="stage", state="done").first()
        assert done.duration_ms is not None and done.duration_ms >= 0

    def test_create_sprite_failure_emits_failed_stage(self, user, fake_sprites):
        session = self._make_session(user)
        fake_sprites.raise_on_create(SpriteError("boom"))
        with pytest.raises(ProvisionError):
            provision_session(user, _spec(user), session_id=str(session.id))
        events = list(
            AgentSessionLog.objects.filter(session=session, kind="stage")
            .order_by("id")
            .values("stage", "state", "data")
        )
        assert events[0] == {"stage": "create_sprite", "state": "started", "data": ""}
        assert events[1]["stage"] == "create_sprite"
        assert events[1]["state"] == "failed"
        assert "boom" in events[1]["data"]

    def test_no_session_id_emits_no_rows(self, user, fake_sprites):
        """Backwards-compat path: tests that don't pass session_id emit nothing."""
        provision_session(user, _spec(user))
        assert AgentSessionLog.objects.count() == 0


class TestProvisionSessionEnvFileShape:
    def test_env_vars_sorted_and_quoted(self, user, fake_sprites):
        env = Environment.objects.create(
            user=user,
            name="e",
            env_vars={"B_SECOND": "two", "A_FIRST": "one with space"},
            version=1,
        )
        provision_session(user, _spec(user, environment=env))
        body = fake_sprites.last_sprite().write_map()["/tmp/aod-env"]
        lines = body.strip().split("\n")
        # Credentials are dumped first (order depends on DB row id; one
        # here), then AOD_SESSION_ID / AOD_MODEL, then env_vars alpha-sorted.
        assert any(line.startswith("ANTHROPIC_API_KEY=") for line in lines)
        assert any(line.startswith("AOD_SESSION_ID=") for line in lines)
        a_idx = next(i for i, line in enumerate(lines) if line.startswith("A_FIRST="))
        b_idx = next(i for i, line in enumerate(lines) if line.startswith("B_SECOND="))
        assert a_idx < b_idx
        assert "'one with space'" in lines[a_idx]
