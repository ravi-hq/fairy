import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import Agent, AgentVersion, APIKey, UserRuntimeKey, UserSpritesKey
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.sprites_exec import SkillSpec, build_wrapper_script


SAMPLE_CONTENT = (
    "---\n"
    "name: web-search\n"
    "description: Search the web for current information.\n"
    "---\n\n"
    "Use this skill when the user asks for up-to-date information.\n"
)


def _skill(name: str = "web-search", content: str = SAMPLE_CONTENT) -> dict:
    return {
        "name": name,
        "description": "Search the web for current information.",
        "content": content,
    }


# --- Fixtures ---


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
def mock_sprites(mocker):
    mock_sprite = mocker.MagicMock()
    mock_fs = mocker.MagicMock()
    mock_sprite.filesystem.return_value = mock_fs
    mock_fs.__truediv__ = mocker.Mock(return_value=mock_fs)
    mock_fs.write_text = mocker.Mock()
    mock_sprite.command.return_value.run = mocker.Mock()
    mock_client = mocker.MagicMock()
    mock_client.create_sprite.return_value = mock_sprite
    mocker.patch("agent_on_demand.views.sessions._get_client", return_value=mock_client)
    mocker.patch("agent_on_demand.views.sessions.threading.Thread")
    return mock_sprite, mock_fs


# --- Wrapper-script skills mechanics ---


class TestWrapperScriptSkills:
    def test_claude_skills_written_to_dot_claude(self):
        config = RUNTIMES["claude"]
        skills = [SkillSpec(name="web-search", content=SAMPLE_CONTENT)]
        script = build_wrapper_script(config, "sk-test", skills=skills)
        assert "mkdir -p /home/sprite/.claude/skills/web-search" in script
        assert "/home/sprite/.claude/skills/web-search/SKILL.md" in script
        assert "Use this skill when the user asks" in script

    def test_claude_oauth_shares_claude_path(self):
        config = RUNTIMES["claude-oauth"]
        skills = [SkillSpec(name="web-search", content=SAMPLE_CONTENT)]
        script = build_wrapper_script(config, "sk-test", skills=skills)
        assert "/home/sprite/.claude/skills/web-search/SKILL.md" in script

    def test_codex_skills_written_to_codex_dir(self):
        config = RUNTIMES["codex"]
        skills = [SkillSpec(name="web-search", content=SAMPLE_CONTENT)]
        script = build_wrapper_script(config, "sk-test", skills=skills)
        assert "/home/sprite/.codex/skills/web-search/SKILL.md" in script
        assert "/home/sprite/.claude/skills" not in script

    def test_gemini_skills_written_to_gemini_dir(self):
        config = RUNTIMES["gemini"]
        skills = [SkillSpec(name="web-search", content=SAMPLE_CONTENT)]
        script = build_wrapper_script(config, "sk-test", skills=skills)
        assert "/home/sprite/.gemini/skills/web-search/SKILL.md" in script

    def test_no_skills_backward_compat(self):
        config = RUNTIMES["claude"]
        script = build_wrapper_script(config, "sk-test")
        assert ".claude/skills" not in script
        assert "SKILL.md" not in script

    def test_multiple_skills_separate_dirs(self):
        config = RUNTIMES["claude"]
        skills = [
            SkillSpec(name="one", content="---\nname: one\ndescription: d\n---\nbody\n"),
            SkillSpec(name="two", content="---\nname: two\ndescription: d\n---\nbody\n"),
        ]
        script = build_wrapper_script(config, "sk-test", skills=skills)
        assert "/home/sprite/.claude/skills/one/SKILL.md" in script
        assert "/home/sprite/.claude/skills/two/SKILL.md" in script

    def test_skills_section_before_exec(self):
        config = RUNTIMES["claude"]
        skills = [SkillSpec(name="web-search", content=SAMPLE_CONTENT)]
        script = build_wrapper_script(config, "sk-test", skills=skills)
        skills_pos = script.index("# Agent skills")
        exec_pos = script.index("exec ")
        assert skills_pos < exec_pos

    def test_skill_content_verbatim_with_shell_metachars(self):
        """Single-quoted heredoc must emit $VAR, backticks, $(cmd) unexpanded."""
        config = RUNTIMES["claude"]
        content = "---\nname: sh\ndescription: d\n---\n$VAR `whoami` $(date)\n"
        skills = [SkillSpec(name="sh", content=content)]
        script = build_wrapper_script(config, "sk-test", skills=skills)
        assert "$VAR `whoami` $(date)" in script


# --- Validator edges ---


@pytest.mark.django_db
class TestCreateAgentWithSkills:
    def test_create_with_skill(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Skill Agent",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "skills": [_skill()],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "web-search"

    def test_create_without_skills_defaults_empty(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Plain",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["skills"] == []


@pytest.mark.django_db
class TestSkillsValidation:
    def _post(self, client, auth_headers, skills):
        return client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Bad",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "skills": skills,
                }
            ),
            content_type="application/json",
            **auth_headers,
        )

    def test_missing_name(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [{"description": "d", "content": "c"}])
        assert resp.status_code == 422
        assert "name" in str(resp.json()["detail"]).lower()

    def test_missing_description(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [{"name": "x", "content": "c"}])
        assert resp.status_code == 422
        assert "description" in str(resp.json()["detail"]).lower()

    def test_missing_content(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [{"name": "x", "description": "d"}])
        assert resp.status_code == 422
        assert "content" in str(resp.json()["detail"]).lower()

    def test_unknown_keys_rejected(self, client: Client, auth_headers):
        skill = _skill()
        skill["scripts"] = {"helper.sh": "echo hi"}
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert "unknown" in str(resp.json()["detail"]).lower()

    def test_name_invalid_chars(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [_skill(name="Web Search")])
        assert resp.status_code == 422
        assert "match" in str(resp.json()["detail"]).lower()

    def test_name_leading_dash(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [_skill(name="-leading")])
        assert resp.status_code == 422

    def test_name_too_long(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [_skill(name="a" * 65)])
        assert resp.status_code == 422

    def test_duplicate_names(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [_skill(name="foo"), _skill(name="foo")])
        assert resp.status_code == 422
        assert "duplicate" in str(resp.json()["detail"]).lower()

    def test_description_too_long(self, client: Client, auth_headers):
        skill = _skill()
        skill["description"] = "x" * 1025
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422

    def test_content_too_large(self, client: Client, auth_headers):
        skill = _skill(content="x" * (64 * 1024 + 1))
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422

    def test_content_contains_heredoc_delimiter(self, client: Client, auth_headers):
        skill = _skill(content="body before SKILL_EOF sneaky after")
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert "SKILL_EOF" in str(resp.json()["detail"])

    def test_max_20_skills(self, client: Client, auth_headers):
        skills = [_skill(name=f"skill-{i}") for i in range(21)]
        resp = self._post(client, auth_headers, skills)
        assert resp.status_code == 422
        assert "20" in str(resp.json()["detail"])

    def test_not_a_list_of_dicts(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, ["not-a-dict"])
        assert resp.status_code == 422


@pytest.mark.django_db
class TestUpdateAgentSkills:
    def test_update_skills_versioned(self, client: Client, auth_headers, user):
        agent = Agent.objects.create(
            user=user,
            name="Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        AgentVersion.objects.create(
            agent=agent,
            version=1,
            name=agent.name,
            model=agent.model,
            runtime=agent.runtime,
        )
        resp = client.put(
            f"/agents/{agent.id}",
            data=json.dumps({"version": 1, "skills": [_skill()]}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 2
        versions = client.get(f"/agents/{agent.id}/versions", **auth_headers).json()["data"]
        assert len(versions) == 2
        assert versions[0]["skills"][0]["name"] == "web-search"
        assert versions[1]["skills"] == []

    def test_update_rejects_invalid_skill(self, client: Client, auth_headers, user):
        agent = Agent.objects.create(
            user=user,
            name="Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        AgentVersion.objects.create(
            agent=agent,
            version=1,
            name=agent.name,
            model=agent.model,
            runtime=agent.runtime,
        )
        resp = client.put(
            f"/agents/{agent.id}",
            data=json.dumps(
                {
                    "version": 1,
                    "skills": [{"name": "Bad Name", "description": "d", "content": "c"}],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422


# --- Session + skills integration ---


@pytest.mark.django_db
class TestSessionSkillsIntegration:
    def test_session_writes_skill_to_wrapper_script(
        self, client: Client, auth_headers, runtime_key, user, mock_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Skilled",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            skills=[_skill()],
        )
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        written_script = mock_fs.write_text.call_args_list[0][0][0]
        assert "mkdir -p /home/sprite/.claude/skills/web-search" in written_script
        assert "/home/sprite/.claude/skills/web-search/SKILL.md" in written_script

    def test_session_agent_without_skills_no_section(
        self, client: Client, auth_headers, runtime_key, user, mock_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Plain",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        written_script = mock_fs.write_text.call_args_list[0][0][0]
        assert "# Agent skills" not in written_script
        assert "SKILL.md" not in written_script

    def test_continue_session_only_writes_prompt_file(
        self, client: Client, auth_headers, runtime_key, user, mocker, mock_sprites
    ):
        """/prompt is a minimal operation; the wrapper script was baked at
        create-time and already carries all skills. We only update the prompt
        file on the Sprite.
        """
        from agent_on_demand.models import AgentSession

        agent = Agent.objects.create(
            user=user,
            name="Skilled",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            skills=[_skill()],
        )
        session = AgentSession.objects.create(
            user=user,
            agent=agent,
            runtime="claude",
            prompt="first",
            sprite_name="sprite-xyz",
            status="completed",
        )
        mock_sprite, mock_fs = mock_sprites
        mocker.patch(
            "agent_on_demand.views.sessions._get_client",
            return_value=mocker.MagicMock(get_sprite=mocker.Mock(return_value=mock_sprite)),
        )
        resp = client.post(
            f"/sessions/{session.id}/prompt",
            data=json.dumps({"prompt": "follow-up"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        assert mock_fs.write_text.call_count == 1
        assert mock_fs.write_text.call_args_list[0][0][0] == "follow-up"
