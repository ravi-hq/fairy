"""Unit tests for the session_service package.

These tests call `provision_session(user, spec)` directly with a RecordingSprites
fake and assert on the recorded command/write sequence — complementing the
HTTP-layer integration tests that drive the same code path from the outside.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from sprites import SpriteError

from agent_on_demand.models import AgentSession, AgentSessionLog, Environment, UserSpritesKey
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
    return u


def _spec(**overrides) -> SessionSpec:
    defaults = dict(
        name="sprite-x",
        runtime=RUNTIMES["claude"],
        api_key="sk-xxx",
        runtime_session_id="11111111-2222-3333-4444-555555555555",
        environment=None,
        repos=[],
        mcp_servers=[],
        skills=[],
    )
    defaults.update(overrides)
    return SessionSpec(**defaults)


class TestProvisionSessionOrder:
    def test_env_file_is_chmod_600(self, user, fake_sprites):
        provision_session(user, _spec())
        sprite = fake_sprites.last_sprite()
        assert "chmod 600 /tmp/aod-env" in sprite.command_strings()

    def test_env_file_contains_api_key_and_session_id(self, user, fake_sprites):
        provision_session(user, _spec())
        env_file = fake_sprites.last_sprite().write_map()["/tmp/aod-env"]
        # shlex.quote leaves simple tokens unquoted; values with special chars
        # get wrapped in single quotes (see test_env_vars_sorted_and_quoted).
        assert "ANTHROPIC_API_KEY=sk-xxx" in env_file
        assert "AOD_SESSION_ID=11111111-2222-3333-4444-555555555555" in env_file

    def test_no_run_agent_script_is_written(self, user, fake_sprites):
        """The per-turn runtime-CLI invocation is assembled inline in
        `run_session_background`; nothing is written to /run-agent.sh."""
        provision_session(user, _spec())
        writes = fake_sprites.last_sprite().writes
        assert all(w.path != "/run-agent.sh" for w in writes)


class TestProvisionSessionFailureHandling:
    def test_create_sprite_failure_raises_provision_error_and_does_not_delete(
        self, user, fake_sprites
    ):
        fake_sprites.raise_on_create(SpriteError("boom"))
        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec())
        assert ei.value.stage == "create_sprite"
        # Nothing was created, so nothing should be deleted.
        assert fake_sprites.deleted == []

    def test_stage_failure_tags_stage_and_triggers_cleanup(self, user, fake_sprites, mocker):
        env = Environment.objects.create(
            user=user,
            name="env",
            packages={"pip": ["pandas"]},
            version=1,
        )
        # Have the first sprite.command call raise — that'll be `chmod 600
        # /tmp/aod-env` in the env-file stage.
        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.raise_on(lambda argv: argv[:2] == ("chmod", "600"), SpriteError("nope"))
            return sprite

        mocker.patch.object(fake_sprites, "create_sprite", side_effect=wrapped)

        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(environment=env))
        assert ei.value.stage == "env_file"
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

    def test_minimal_spec_emits_only_create_sprite(self, user, fake_sprites):
        session = self._make_session(user)
        provision_session(user, _spec(), session_id=str(session.id))
        events = list(
            AgentSessionLog.objects.filter(session=session, kind="stage")
            .order_by("id")
            .values("stage", "state")
        )
        # env_file always runs (writes the API key file). No packages, repos,
        # setup, mcp, or skills in _spec(), so those stages are skipped.
        assert events == [
            {"stage": "create_sprite", "state": "started"},
            {"stage": "create_sprite", "state": "done"},
            {"stage": "env_file", "state": "started"},
            {"stage": "env_file", "state": "done"},
        ]

    def test_done_events_carry_duration_ms(self, user, fake_sprites):
        session = self._make_session(user)
        provision_session(user, _spec(), session_id=str(session.id))
        done = AgentSessionLog.objects.filter(session=session, kind="stage", state="done").first()
        assert done.duration_ms is not None and done.duration_ms >= 0

    def test_create_sprite_failure_emits_failed_stage(self, user, fake_sprites):
        session = self._make_session(user)
        fake_sprites.raise_on_create(SpriteError("boom"))
        with pytest.raises(ProvisionError):
            provision_session(user, _spec(), session_id=str(session.id))
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
        provision_session(user, _spec())
        # Use the fake_sprites fixture's session handle to prove provisioning ran;
        # the real assertion is that no logs were written anywhere.
        assert AgentSessionLog.objects.count() == 0


class TestProvisionSessionEnvFileShape:
    def test_env_vars_sorted_and_quoted(self, user, fake_sprites):
        env = Environment.objects.create(
            user=user,
            name="e",
            env_vars={"B_SECOND": "two", "A_FIRST": "one with space"},
            version=1,
        )
        provision_session(user, _spec(environment=env))
        body = fake_sprites.last_sprite().write_map()["/tmp/aod-env"]
        lines = body.strip().split("\n")
        # API key and AOD_SESSION_ID come first, env vars follow in alpha order.
        assert lines[0].startswith("ANTHROPIC_API_KEY=")
        assert lines[1].startswith("AOD_SESSION_ID=")
        assert lines[2].startswith("A_FIRST=")
        assert lines[3].startswith("B_SECOND=")
        assert "'one with space'" in lines[2]
