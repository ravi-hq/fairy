"""HTTP-layer validation for the agent `skills` field.

Per-runtime skills materialization onto the Sprite is exercised via the
provision flow in `tests/test_session_service.py` and in per-runtime tests.
"""

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import Agent, AgentVersion, APIKey


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


@pytest.mark.django_db
class TestCreateAgentWithSkills:
    def test_create_with_skill(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Skill Agent",
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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

    @pytest.mark.parametrize("field", ["name", "description", "content"])
    def test_inline_field_must_be_string(self, client: Client, auth_headers, field):
        """Each required inline-skill field must be a string. A numeric
        value present (so the missing-key branch doesn't catch it) must
        still reject — without this, an SDK that sent ``description=42``
        by mistake would 500 downstream when the validator tried to
        len()/encode() it."""
        skill = _skill()
        skill[field] = 42
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert field in str(resp.json()["detail"])
        assert "must be a string" in str(resp.json()["detail"])

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
            model="anthropic/claude-sonnet-4-6",
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
            model="anthropic/claude-sonnet-4-6",
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


def _github_skill(
    name: str | None = "aod-sdk-python",
    source: str = "ravi-hq/agent-on-demand",
) -> dict:
    payload: dict = {
        "type": "github",
        "description": "Install aod-sdk-python skill from the AoD repo.",
        "source": source,
    }
    if name is not None:
        payload["name"] = name
    return payload


@pytest.mark.django_db
class TestGithubSkillValidation:
    """Validation for the github skill reference shape.

    Github references are installed on the Sprite at provision time via the
    skills.sh CLI (`npx skills add <source> -g -a <runtime-agent> -y`), so
    the API only needs to validate the shape — not resolve any content.
    """

    def _post(self, client, auth_headers, skills):
        return client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "GH Agent",
                    "model": "anthropic/claude-sonnet-4-6",
                    "runtime": "claude",
                    "skills": skills,
                }
            ),
            content_type="application/json",
            **auth_headers,
        )

    def test_create_with_github_skill(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [_github_skill()])
        assert resp.status_code == 201
        data = resp.json()
        assert data["skills"] == [_github_skill()]

    def test_inline_and_github_skill_together(self, client: Client, auth_headers):
        resp = self._post(client, auth_headers, [_skill(), _github_skill()])
        assert resp.status_code == 201
        assert len(resp.json()["skills"]) == 2

    def test_rejects_unknown_type(self, client: Client, auth_headers):
        skill = _github_skill()
        skill["type"] = "gitlab"
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert "github" in str(resp.json()["detail"]).lower()

    def test_missing_source(self, client: Client, auth_headers):
        skill = _github_skill()
        del skill["source"]
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert "source" in str(resp.json()["detail"]).lower()

    def test_invalid_source_format(self, client: Client, auth_headers):
        skill = _github_skill(source="not-a-valid-source")
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert "owner/repo" in str(resp.json()["detail"])

    def test_rejects_content_field(self, client: Client, auth_headers):
        skill = _github_skill()
        skill["content"] = "body"
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert "unknown" in str(resp.json()["detail"]).lower()

    def test_rejects_ref_field_in_v1(self, client: Client, auth_headers):
        # v1 does not support ref pinning — the skills.sh shorthand doesn't
        # either. Accepting `ref` silently would mislead callers.
        skill = _github_skill()
        skill["ref"] = "main"
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert "unknown" in str(resp.json()["detail"]).lower()

    def test_name_still_validated(self, client: Client, auth_headers):
        skill = _github_skill(name="Bad Name")
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422

    @pytest.mark.parametrize("field", ["type", "source"])
    def test_required_field_must_be_string(self, client: Client, auth_headers, field):
        """Required github-skill fields (type, source) must be strings.
        Pin the type-mismatch path so a numeric or boolean value rejects
        with a 422 rather than crashing downstream when source is later
        regex-matched or type is compared."""
        skill = _github_skill()
        skill[field] = 42
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert field in str(resp.json()["detail"])
        assert "must be a string" in str(resp.json()["detail"])

    def test_name_when_present_must_be_string(self, client: Client, auth_headers):
        """`name` is optional for github skills — but when supplied it
        must be a string. Without this branch, a numeric `name` would
        slip past the optional-string check and crash later in dedup
        or skills.sh `--skill <name>` shell-escaping."""
        skill = _github_skill()
        skill["name"] = 42
        resp = self._post(client, auth_headers, [skill])
        assert resp.status_code == 422
        assert "name" in str(resp.json()["detail"])
        assert "must be a string" in str(resp.json()["detail"])

    def test_duplicate_names_across_shapes(self, client: Client, auth_headers):
        # Name is the dedup key regardless of inline vs github shape.
        inline = _skill(name="shared")
        github = _github_skill(name="shared")
        resp = self._post(client, auth_headers, [inline, github])
        assert resp.status_code == 422
        assert "duplicate" in str(resp.json()["detail"]).lower()

    def test_github_skill_without_name_is_accepted(self, client: Client, auth_headers):
        # Omitting name == "install every SKILL.md from the repo".
        resp = self._post(client, auth_headers, [_github_skill(name=None)])
        assert resp.status_code == 201, resp.json()
        assert resp.json()["skills"][0] == {
            "type": "github",
            "description": "Install aod-sdk-python skill from the AoD repo.",
            "source": "ravi-hq/agent-on-demand",
        }

    def test_two_whole_repo_entries_for_same_source_collide(self, client: Client, auth_headers):
        skill = _github_skill(name=None)
        resp = self._post(client, auth_headers, [skill, skill])
        assert resp.status_code == 422
        assert "duplicate" in str(resp.json()["detail"]).lower()

    def test_named_and_whole_repo_entries_for_same_source_coexist(
        self, client: Client, auth_headers
    ):
        # `--skill foo` install and a `--all from same repo` install have
        # different dedup keys; both should be accepted on one agent.
        named = _github_skill(name="aod-sdk-python")
        whole = _github_skill(name=None)
        resp = self._post(client, auth_headers, [named, whole])
        assert resp.status_code == 201, resp.json()
        assert len(resp.json()["skills"]) == 2
