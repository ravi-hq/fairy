import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import Agent, AgentVersion, APIKey, UserRuntimeKey, UserSpritesKey


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


# --- Session + skills integration (via recording fake) ---


def _last_sprite_writes(fake_sprites) -> dict[str, str]:
    return fake_sprites.last_sprite().write_map()


@pytest.mark.django_db
class TestSessionSkillsIntegration:
    def test_claude_skill_writes_skill_md_under_dot_claude(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Skilled",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            skills=[_skill()],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        sprite = fake_sprites.last_sprite()
        assert "/home/sprite/.claude/skills/web-search/SKILL.md" in sprite.write_map()
        cmds = sprite.command_strings()
        assert "mkdir -p /home/sprite/.claude/skills/web-search" in cmds

    def test_codex_skill_writes_under_dot_codex(
        self, client: Client, auth_headers, user, sprites_key, fake_sprites
    ):
        urk = UserRuntimeKey(user=user, runtime="codex")
        urk.set_api_key("k")
        urk.save()
        agent = Agent.objects.create(
            user=user,
            name="Codex Skilled",
            model="gpt-4.1",
            runtime="codex",
            version=1,
            skills=[_skill()],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        writes = _last_sprite_writes(fake_sprites)
        assert "/home/sprite/.codex/skills/web-search/SKILL.md" in writes
        assert "/home/sprite/.claude/skills/web-search/SKILL.md" not in writes

    def test_gemini_skill_writes_under_dot_gemini(
        self, client: Client, auth_headers, user, sprites_key, fake_sprites
    ):
        urk = UserRuntimeKey(user=user, runtime="gemini")
        urk.set_api_key("k")
        urk.save()
        agent = Agent.objects.create(
            user=user,
            name="Gemini Skilled",
            model="gemini-2.5-pro",
            runtime="gemini",
            version=1,
            skills=[_skill()],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        writes = _last_sprite_writes(fake_sprites)
        assert "/home/sprite/.gemini/skills/web-search/SKILL.md" in writes

    def test_agent_without_skills_writes_none(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Plain",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        writes = _last_sprite_writes(fake_sprites)
        assert not any("SKILL.md" in path for path in writes)

    def test_multiple_skills_each_get_their_own_dir(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Multi",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            skills=[
                _skill(name="one", content="---\nname: one\ndescription: d\n---\nbody\n"),
                _skill(name="two", content="---\nname: two\ndescription: d\n---\nbody\n"),
            ],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        writes = _last_sprite_writes(fake_sprites)
        assert "/home/sprite/.claude/skills/one/SKILL.md" in writes
        assert "/home/sprite/.claude/skills/two/SKILL.md" in writes

    def test_skill_content_written_verbatim(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
    ):
        """Skills are written via the filesystem API now, so any shell-metachar
        concerns from the old heredoc approach no longer apply — the content is
        raw bytes from the Python side to the Sprite filesystem."""
        content = "---\nname: sh\ndescription: d\n---\n$VAR `whoami` $(date)\n"
        agent = Agent.objects.create(
            user=user,
            name="Raw",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            skills=[_skill(name="sh", content=content)],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        writes = _last_sprite_writes(fake_sprites)
        assert writes["/home/sprite/.claude/skills/sh/SKILL.md"] == content

    def test_continue_session_only_writes_prompt_file(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
    ):
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
        resp = client.post(
            f"/sessions/{session.id}/prompt",
            data=json.dumps({"prompt": "follow-up"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        sprite = fake_sprites.sprites["sprite-xyz"]
        assert len(sprite.writes) == 1
        assert sprite.writes[0].path == "/tmp/aod-prompt.txt"


# --- Validator edges (HTTP layer, unchanged) ---


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
