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
    UserBackendCredential,
    UserCredential,
)
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service import (
    ProvisionError,
    SessionSpec,
    provision_session,
)
from agent_on_demand.session_service.specs import SkillSpec


@pytest.fixture
def user(db):
    u = User.objects.create_user(username="svc", password="p")
    bcred = UserBackendCredential(user=u, backend="sprites")
    bcred.set_token("fake-sprites-token")
    bcred.save()
    cred = UserCredential(user=u, kind="provider:anthropic")
    cred.set_value("sk-xxx")
    cred.save()
    return u


def _spec(user, **overrides) -> SessionSpec:
    defaults = {
        "name": "sprite-x",
        "runtime": RUNTIMES["claude"],
        "model": "anthropic/claude-sonnet-4-6",
        "user": user,
        "runtime_session_id": "11111111-2222-3333-4444-555555555555",
        "environment": None,
        "repos": [],
        "mcp_servers": [],
        "skills": [],
    }
    defaults.update(overrides)
    return SessionSpec(**defaults)


class TestProvisionSessionOrder:
    def test_env_file_chmod_is_in_provision_script(self, user, fake_sprites):
        provision_session(user, _spec(user))
        sprite = fake_sprites.last_sprite()
        script = sprite.write_map()["/tmp/aod-provision.sh"]
        assert "chmod 600 /tmp/aod-env" in script

    @pytest.mark.parametrize(
        "runtime_name,expected_dir",
        [
            ("codex", "/home/sprite/.codex"),
            ("gemini", "/home/sprite/.gemini"),
            ("opencode", "/home/sprite/.config/opencode"),
        ],
    )
    def test_mcp_servers_mkdir_in_provision_script_per_runtime(
        self, user, fake_sprites, runtime_name, expected_dir
    ):
        """Runtimes whose MCP config file lives under a non-default directory
        need that directory created before the post-script ``fs.write``.
        Without the mkdir, the runtime config write fails with stage=
        runtime_config and the session never reaches running. Claude is
        excluded — its config goes to ``/home/sprite/.claude.json`` and the
        parent already exists."""
        from agent_on_demand.session_service.specs import McpServerSpec

        mcp_servers = [McpServerSpec(name="srv", type="url", url="https://example.com/mcp")]
        provision_session(
            user,
            _spec(user, runtime=RUNTIMES[runtime_name], mcp_servers=mcp_servers),
        )
        script = fake_sprites.last_sprite().write_map()["/tmp/aod-provision.sh"]
        assert f"mkdir -p {expected_dir}" in script

    def test_no_mcp_servers_no_runtime_mkdir(self, user, fake_sprites):
        """Without MCP servers, the runtime-specific config dirs are not
        created — the mkdir line is only emitted for post-script writes that
        actually need the parent dir to exist."""
        provision_session(user, _spec(user, runtime=RUNTIMES["codex"]))
        script = fake_sprites.last_sprite().write_map()["/tmp/aod-provision.sh"]
        assert "/home/sprite/.codex" not in script

    def test_provisioning_issues_install_then_provision_script(self, user, fake_sprites):
        provision_session(user, _spec(user))
        sprite = fake_sprites.last_sprite()
        # Two sprite.command invocations during provisioning, in order:
        # ClaudeRuntime.install runs `claude update` to upgrade past the
        # base-image 2.1.92 (predates Traces (beta) TRACEPARENT support);
        # the provision script bundles the rest. No per-stage chmod /
        # clone commands.
        assert sprite.command_strings() == [
            "bash -lc claude update",
            "bash -l /tmp/aod-provision.sh",
        ]

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

    def test_install_runtime_sprite_error_tags_stage(self, user, fake_sprites, mocker):
        """Runtime-install failure must surface as ProvisionError(stage=
        install_runtime). Without this, a meta-runtime npm-install hiccup
        would fall through to the generic SpriteError catch and report
        the wrong stage in the failed-stage telemetry."""
        from agent_on_demand.runtimes import RUNTIMES

        opencode = RUNTIMES["opencode"]
        mocker.patch.object(opencode, "install", side_effect=SpriteError("npm boom"))
        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user, runtime=opencode))
        assert ei.value.stage == "install_runtime"
        # Sprite was created, then deleted as best-effort cleanup.
        assert fake_sprites.deleted == ["sprite-x"]

    def test_apply_network_policy_sprite_error_tags_stage(self, user, fake_sprites):
        """If sprite.update_network_policy raises, ProvisionError must tag
        stage=network_policy so failed-stage telemetry attributes the
        outage to the right subsystem."""
        env = Environment.objects.create(
            user=user,
            name="limited-env",
            networking_type="limited",
            networking_config={"allowed_hosts": ["api.example.com"]},
            version=1,
        )
        # Wrap create_sprite so the recording sprite raises when policy is set.
        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.raise_on("update_network_policy", SpriteError("policy fail"))
            return sprite

        fake_sprites.create_sprite = wrapped  # patch directly, no mocker needed

        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user, environment=env))
        assert ei.value.stage == "network_policy"

    def test_write_env_file_sprite_error_tags_stage(self, user, fake_sprites):
        """A SpriteError raised during the env-file write must surface as
        ProvisionError(stage=env_file). Without this branch covered, a
        refactor that didn't wrap SpriteError here would leak it as an
        opaque 500 from the worker."""
        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.filesystem().raise_on_write("aod-env", SpriteError("disk fail"))
            return sprite

        fake_sprites.create_sprite = wrapped

        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user))
        assert ei.value.stage == "env_file"
        assert fake_sprites.deleted == ["sprite-x"]

    def test_write_git_credentials_sprite_error_tags_stage(self, user, fake_sprites):
        """A SpriteError raised during the .git-credentials write must
        surface as stage=git_credentials. Reaches this branch by giving
        the spec a repo with a token — without a token, no creds file
        is written and the branch is skipped."""
        from agent_on_demand.session_service.specs import RepoSpec

        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.filesystem().raise_on_write(".git-credentials", SpriteError("disk fail"))
            return sprite

        fake_sprites.create_sprite = wrapped

        repos = [
            RepoSpec(
                url="https://github.com/owner/repo",
                mount_path="/repos/repo",
                token="ghp_fake",
            )
        ]
        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user, repos=repos))
        assert ei.value.stage == "git_credentials"
        assert fake_sprites.deleted == ["sprite-x"]

    def test_unwrapped_sprite_error_falls_back_to_unknown_stage(self, user, fake_sprites, mocker):
        """Each per-stage helper wraps its own SpriteError as
        ProvisionError(stage=...). If a future helper forgets that wrap and
        leaks SpriteError, the catch-all in provision_session must surface
        it as ProvisionError(stage="unknown") *and* still trigger
        best_effort_delete — otherwise the orphaned Sprite leaks.

        Reaches the branch by patching one of the helpers (`install_runtime`)
        to raise SpriteError directly, bypassing its own wrapper."""
        from agent_on_demand.session_service.provisioning import orchestrator

        mocker.patch.object(orchestrator, "install_runtime", side_effect=SpriteError("unwrapped"))

        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user))
        assert ei.value.stage == "unknown"
        assert "Failed to prepare Sprite: unwrapped" in str(ei.value)
        # The orphaned Sprite must be cleaned up — without that, every
        # unwrapped helper leak permanently leaks a Sprite per call.
        assert fake_sprites.deleted == ["sprite-x"]

    def test_provision_setup_failure_includes_stderr_tail_in_detail(
        self, user, fake_sprites, mocker
    ):
        """When the provision script fails, ProvisionError.detail must
        include the captured stderr tail. Without that branch, operators
        see only "Provisioning script failed: <SpriteError msg>" — apt or
        git errors that are critical to triage stay hidden behind the
        opaque transport error.

        Reaches the branch by arranging for the bash invocation to raise
        SpriteError after the fake writes a stderr blob to the buffer
        the provisioning code assigned to `cmd.stderr`."""
        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.raise_on(
                lambda argv: argv[:2] == ("bash", "-l"),
                SpriteError("exit 1"),
                stderr=b"E: Unable to locate package not-a-real-pkg\n",
            )
            return sprite

        mocker.patch.object(fake_sprites, "create_sprite", side_effect=wrapped)

        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user))
        assert ei.value.stage == "provision_setup"
        # Both the SpriteError message and the captured stderr tail must
        # appear in the detail — operators rely on the stderr to triage.
        assert "Provisioning script failed: exit 1" in str(ei.value)
        assert "stderr:\nE: Unable to locate package not-a-real-pkg" in str(ei.value)

    def test_write_runtime_config_sprite_error_tags_stage(self, user, fake_sprites):
        """A SpriteError raised while the runtime writes its config (claude's
        ``.claude.json`` with mcp_servers) must surface as stage=runtime_config.
        Without this branch covered, a refactor that drops the SpriteError
        wrapper here would leak an opaque 500 instead of a stage-tagged
        ProvisionError, and failed-stage telemetry would attribute the outage
        to the wrong subsystem."""
        from agent_on_demand.session_service.specs import McpServerSpec

        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.filesystem().raise_on_write(".claude.json", SpriteError("disk fail"))
            return sprite

        fake_sprites.create_sprite = wrapped

        mcp_servers = [McpServerSpec(name="srv", type="url", url="https://example.com/mcp")]
        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user, mcp_servers=mcp_servers))
        assert ei.value.stage == "runtime_config"
        assert fake_sprites.deleted == ["sprite-x"]

    def test_write_skills_sprite_error_tags_stage(self, user, fake_sprites):
        """A SpriteError raised while materializing an inline skill must
        surface as stage=skills. Reaches the branch via an inline SkillSpec —
        github skills install via shell-out and would surface SpriteError
        through the command path instead."""
        original_create = fake_sprites.create_sprite

        def wrapped(name):
            sprite = original_create(name)
            sprite.filesystem().raise_on_write("SKILL.md", SpriteError("disk fail"))
            return sprite

        fake_sprites.create_sprite = wrapped

        skill = SkillSpec(name="inline-skill", content="---\nname: inline-skill\n---\n")
        with pytest.raises(ProvisionError) as ei:
            provision_session(user, _spec(user, skills=[skill]))
        assert ei.value.stage == "skills"
        assert fake_sprites.deleted == ["sprite-x"]


class TestDestroyAndResumeSession:
    """destroy_session is a best-effort cleanup; resume_session is the
    re-attach for a still-running Sprite. Each has narrow defensive
    branches that previously had no tests."""

    def test_destroy_session_no_handle_is_noop(self, user, fake_sprites):
        """A session with empty backend_handle (e.g. terminate already
        cleared it) must not invoke delete — otherwise we'd queue cleanup
        for a non-existent Sprite and inflate Sprites-API error rates."""
        from agent_on_demand.session_service.provisioning import destroy_session

        destroy_session(user, "")
        assert fake_sprites.deleted == []

    def test_destroy_session_no_client_logs_and_returns(self, user, mocker, caplog):
        """When the user has no Sprites key at delete time, destroy_session
        must log + return rather than raising — otherwise a key-rotation
        race would crash the cleanup task."""
        import logging

        from agent_on_demand.session_service.provisioning import destroy_session

        mocker.patch("agent_on_demand.session_service.client.get_client", return_value=None)
        mocker.patch("agent_on_demand.session_service.get_client", return_value=None)
        with caplog.at_level(logging.WARNING):
            destroy_session(user, "aod-orphan")
        assert any("no backend credentials" in r.message for r in caplog.records)

    def test_resume_session_wraps_sprite_error_in_session_handle_not_found(
        self, user, fake_sprites, mocker
    ):
        """If get_sprite raises (Sprite expired or never existed), resume
        must surface SessionHandleNotFound — the views layer maps that to
        a 410 distinct from a 500."""
        from agent_on_demand.session_service.errors import SessionHandleNotFound
        from agent_on_demand.session_service.provisioning import resume_session

        mocker.patch.object(fake_sprites, "get_sprite", side_effect=SpriteError("gone"))
        with pytest.raises(SessionHandleNotFound):
            resume_session(user, "aod-gone")


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


class TestProvisionSkills:
    """write_skills materializes both inline and github skills on the Sprite.

    Inline skills are written verbatim to
    `<skills_root>/<name>/SKILL.md`. Github skills shell out to
    `npx -y skills@latest add <source> --global --agent <runtime-agent> --yes`
    — the `skills.sh <https://skills.sh>`_ CLI handles discovery + install.
    """

    SAMPLE_CONTENT = "---\nname: inline-skill\ndescription: Local inline skill.\n---\n\nBody.\n"

    def test_inline_skill_written_to_runtime_skills_root(self, user, fake_sprites):
        spec = _spec(
            user,
            skills=[SkillSpec(name="inline-skill", content=self.SAMPLE_CONTENT)],
        )
        provision_session(user, spec)
        writes = fake_sprites.last_sprite().write_map()
        assert writes["/home/sprite/.claude/skills/inline-skill/SKILL.md"] == self.SAMPLE_CONTENT

    def test_github_skill_invokes_skills_sh_cli_with_skill_flag(self, user, fake_sprites):
        spec = _spec(
            user,
            skills=[SkillSpec(name="aod-sdk-python", source="ravi-hq/agent-on-demand")],
        )
        provision_session(user, spec)
        shell_lines = fake_sprites.last_sprite().shell_strings()
        # When a name is provided we MUST pass --skill so only that one
        # SKILL.md from the repo is installed (not every skill in the repo).
        assert any(
            "npx -y skills@latest add ravi-hq/agent-on-demand "
            "--global --agent claude-code --yes --skill aod-sdk-python" in line
            for line in shell_lines
        ), f"no skills.sh install command recorded; got: {shell_lines!r}"

    def test_github_skill_without_name_installs_whole_repo(self, user, fake_sprites):
        # Omitting `name` is the explicit signal "install every skill from
        # this repo" — no `--skill` flag should appear.
        spec = _spec(
            user,
            skills=[SkillSpec(source="ravi-hq/agent-on-demand")],
        )
        provision_session(user, spec)
        shell_lines = fake_sprites.last_sprite().shell_strings()
        install_lines = [line for line in shell_lines if "npx -y skills@latest add" in line]
        assert len(install_lines) == 1, install_lines
        assert "--skill" not in install_lines[0]
        assert (
            "npx -y skills@latest add ravi-hq/agent-on-demand --global --agent claude-code --yes"
        ) in install_lines[0]

    @pytest.mark.parametrize(
        "runtime_name,expected_agent",
        [
            ("claude", "claude-code"),
            ("codex", "codex"),
            ("gemini", "gemini-cli"),
            ("opencode", "opencode"),
        ],
    )
    def test_github_skill_uses_runtime_specific_agent_identifier(
        self, user, fake_sprites, runtime_name, expected_agent
    ):
        # write_skills only reads `runtime.skills_sh_agent` and the skill
        # source; no credentials or env setup are needed, so call it directly
        # instead of running full provision_session.
        from agent_on_demand.session_service.provisioning.stages import write_skills
        from tests.fakes.sprite import RecordingSprite

        sprite = RecordingSprite("s")
        spec = _spec(
            user,
            runtime=RUNTIMES[runtime_name],
            skills=[SkillSpec(name="aod", source="ravi-hq/agent-on-demand")],
        )
        write_skills(sprite, spec, session_id=None)
        shell_lines = sprite.shell_strings()
        assert any(f"--agent {expected_agent} --yes" in line for line in shell_lines), shell_lines

    def test_github_skill_source_is_shell_quoted(self, user, fake_sprites):
        # `source` is validated to be owner/repo, so no shell metacharacters
        # can sneak in — but the call site still quotes it for defense in
        # depth. Assert that no command substitutions appear in the output.
        spec = _spec(
            user,
            skills=[SkillSpec(name="aod", source="ravi-hq/agent-on-demand")],
        )
        provision_session(user, spec)
        for line in fake_sprites.last_sprite().shell_strings():
            assert "`" not in line
            assert "$(" not in line

    def test_mixed_inline_and_github_skills(self, user, fake_sprites):
        spec = _spec(
            user,
            skills=[
                SkillSpec(name="inline-skill", content=self.SAMPLE_CONTENT),
                SkillSpec(name="from-gh", source="ravi-hq/agent-on-demand"),
            ],
        )
        provision_session(user, spec)
        sprite = fake_sprites.last_sprite()
        assert (
            sprite.write_map()["/home/sprite/.claude/skills/inline-skill/SKILL.md"]
            == self.SAMPLE_CONTENT
        )
        assert any(
            "npx -y skills@latest add ravi-hq/agent-on-demand" in line
            for line in sprite.shell_strings()
        )

    def test_no_skills_no_install_command(self, user, fake_sprites):
        provision_session(user, _spec(user, skills=[]))
        shell_lines = fake_sprites.last_sprite().shell_strings()
        assert not any("npx -y skills@latest add" in line for line in shell_lines)
