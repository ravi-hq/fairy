---
date: 2026-04-16T17:30:00-07:00
researcher: Claude Code (direct research)
git_commit: 3927cf98f05e45d9287c48dff25e6795e9173a52
branch: main
topic: "Environment model for container setup: packages, env vars, networking, setup script"
tags: [research, environments, container-setup, managed-agents]
status: complete
method: direct
last_updated: 2026-04-16
last_updated_by: Claude Code
---

# Research: Environment Model for Container Setup

**Date**: 2026-04-16
**Git Commit**: [`3927cf9`](3927cf98f05e45d9287c48dff25e6795e9173a52)
**Branch**: `main`

## Research Question

How should fairy implement an Environment model that handles setup scripts, environment variables, package installation, and networking configuration — mirroring the Anthropic Managed Agents environment API?

## Summary

The fairy codebase currently has no environment abstraction. Container setup is hardcoded in `sprites_exec.py:build_wrapper_script` which only handles API key export, git init, repo cloning, and exec. An Environment model would sit between Agent and Session — agents optionally reference an environment, sessions resolve the environment at creation time, and the wrapper script injects the environment's packages/env vars/setup script before running the agent command. The Sprites container API (filesystem writes + command execution) already supports everything needed for implementation.

## Track 1: Data Model & API Surface

### Current Model Hierarchy

```
User ──┬── APIKey (auth)
       ├── UserRuntimeKey (encrypted runtime API keys)
       ├── Agent (template: name, system prompt, model, runtime, skills, metadata)
       │   └── AgentVersion (immutable snapshots)
       └── AgentSession (execution instance)
           ├── SessionResource (github repos)
           └── AgentSessionLog (stdout/stderr chunks)
```

### Where Environment Fits

The Anthropic API has environments as a top-level resource owned by the organization. In fairy, environments should be owned by the user (consistent with Agent, APIKey, etc.):

```
User ──┬── Environment (reusable container config)
       ├── Agent (can optionally reference an environment)
       └── AgentSession (resolves environment at creation)
```

### Anthropic API Mapping

| Anthropic Field | Fairy Model Field | Notes |
|---|---|---|
| `name` | `name` (CharField, unique per user) | Required, used for lookup |
| `config.type` | Always "cloud" | Fairy only supports Sprites containers |
| `config.packages.pip` | `packages` (JSONField) | `{"pip": [...], "npm": [...], "apt": [...]}` |
| `config.packages.npm` | (same JSONField) | Nested under packages |
| `config.packages.apt` | (same JSONField) | Nested under packages |
| `config.networking.type` | `networking_type` (CharField) | "unrestricted" or "limited" |
| `config.networking.allowed_hosts` | `networking_config` (JSONField) | `{"allowed_hosts": [...], ...}` |
| — | `setup_script` (TextField) | Not in Anthropic API — fairy extension for arbitrary shell setup |
| — | `env_vars` (JSONField) | `{"KEY": "value"}` — fairy extension |

### Fields NOT in Anthropic API but Needed

1. **`setup_script`** — Arbitrary bash commands run after packages install, before the agent starts. Use cases: configuring tools, writing config files, setting up databases.
2. **`env_vars`** — Additional environment variables exported before the agent command. The runtime API key is already handled separately via `UserRuntimeKey`, but users may need `DATABASE_URL`, `REDIS_URL`, etc.
3. **`archived_at`** — Matches the Agent lifecycle pattern. Archived environments can't be used for new sessions.

### Environment Lifecycle (mirrors Anthropic)

- **Create**: `POST /environments` — name must be unique per user
- **List**: `GET /environments` — excludes archived
- **Retrieve**: `GET /environments/{id}`
- **Update**: `PUT /environments/{id}` — with optimistic concurrency (version field)
- **Archive**: `POST /environments/{id}/archive` — read-only, existing sessions continue
- **Delete**: `DELETE /environments/{id}` — only if no sessions reference it

## Track 2: Container Setup & Execution

### Current Wrapper Script Structure (`sprites_exec.py:50-83`)

```bash
#!/bin/bash
set -euo pipefail
export ANTHROPIC_API_KEY='...'           # 1. API key
export PROMPT='...'                       # 2. Prompt

cd /home/sprite                           # 3. Working directory
mkdir -p .gemini
if [ ! -d .git ]; then                    # 4. Git init
    git init -q
    git add -A 2>/dev/null || true
    git commit -q -m "init" --allow-empty 2>/dev/null || true
fi

# Clone GitHub repositories              # 5. Resource cloning (if repos)
git clone --depth=1 --quiet ...

exec claude --print ...                   # 6. Run agent
```

### Where Environment Setup Inserts

The wrapper script needs new sections between steps 4 and 6:

```bash
#!/bin/bash
set -euo pipefail
export ANTHROPIC_API_KEY='...'           # 1. Runtime API key (existing)
export PROMPT='...'                       # 2. Prompt (existing)
export DATABASE_URL='...'                 # 3. NEW: env_vars from environment
export REDIS_URL='...'

cd /home/sprite                           # 4. Working directory (existing)
mkdir -p .gemini
if [ ! -d .git ]; then ... fi

# Install packages                        # 5. NEW: packages from environment
apt-get update -qq && apt-get install -y -qq ffmpeg
pip install --quiet pandas numpy
npm install --global express

# Clone GitHub repositories              # 6. Resource cloning (existing)
git clone --depth=1 --quiet ...

# Custom setup script                     # 7. NEW: setup_script from environment
echo "Running custom setup..."
createdb myapp

exec claude --print ...                   # 8. Run agent (existing)
```

### Package Installation Order

Anthropic docs specify alphabetical order: apt, cargo, gem, go, npm, pip. This matters because:
- `apt` packages may be prerequisites for pip packages (e.g., `libpq-dev` before `psycopg2`)
- `npm` global installs go to a system path that's already in `$PATH`

### Networking Considerations

Sprites containers use the Sprites platform networking. Fairy doesn't directly control iptables or network namespaces. Options:

1. **Store networking config on the model, validate at session creation** — The Environment stores the desired networking mode, and fairy validates it but relies on Sprites for enforcement.
2. **Use `sprite.command()` to configure iptables** — If Sprites containers run as root, fairy could inject firewall rules. But this is fragile and Sprites-specific.
3. **Pass networking config to Sprites API** — If Sprites supports network policies, pass them through. Need to check Sprites API.

**Recommendation**: Store networking config on the Environment model. For v1, validate and store it but don't enforce it at the container level (Sprites doesn't expose this). Add enforcement later when Sprites supports it or when we move to a different container runtime.

### `build_wrapper_script` Changes Needed

Current signature:
```python
def build_wrapper_script(config, api_key, prompt, *, continue_session=False, repos=None)
```

New signature:
```python
def build_wrapper_script(config, api_key, prompt, *, continue_session=False, repos=None, environment=None)
```

Where `environment` is an `EnvironmentConfig` dataclass (or the model instance) containing packages, env_vars, setup_script.

## Track 3: Session-Environment Integration

### Session Creation Flow (`views.py:131-240`)

Current resolution order:
1. Parse `RunRequest` (runtime, prompt, agent_id, resources, timeout)
2. Resolve agent if `agent_id` provided
3. Resolve runtime: explicit > agent > error
4. Look up user's runtime API key
5. Create Sprite → write wrapper script → create session → start background thread

With environments:
1. Parse `RunRequest` — add optional `environment_id`
2. Resolve agent if `agent_id` provided
3. **Resolve environment**: explicit `environment_id` > agent's `environment_id` > None
4. Resolve runtime: explicit > agent > error
5. Look up user's runtime API key
6. Create Sprite → write wrapper script **with environment config** → create session (with `environment` FK) → start background thread

### Agent-Environment Relationship

Two options:
1. **Agent has optional `environment_id` FK** — Agent templates include a default environment. Sessions inherit it unless overridden.
2. **Environment only on sessions** — Agents don't reference environments; callers always specify at session creation.

**Recommendation**: Option 1 (Agent has optional `environment_id`). This matches the Anthropic pattern where agents are configured with environments, and sessions inherit the agent's environment.

### Session Model Changes

Add to `AgentSession`:
```python
environment = models.ForeignKey(
    "Environment", on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions"
)
```

`SET_NULL` because deleting an environment shouldn't cascade to sessions — historical sessions should keep their records.

### `RunRequest` Changes

```python
class RunRequest(BaseModel):
    runtime: str | None = None
    prompt: str
    timeout: int = Field(default=600, ge=10, le=3600)
    agent_id: str | None = None
    environment_id: str | None = None  # NEW
    resources: list[GitHubRepoResource] = Field(default_factory=list)
```

### Sensitive Data Handling

`env_vars` may contain secrets (database passwords, API keys). Two approaches:
1. **Store in plaintext JSON** — Simple, consistent with how `metadata` works on Agent. Rely on DB-level encryption.
2. **Encrypt sensitive values** — Use the existing `fairy.crypto.encrypt/decrypt` pattern from `UserRuntimeKey`.

**Recommendation**: Store as plaintext JSON for v1. The existing pattern for sensitive per-user data (UserRuntimeKey) uses encryption, but env_vars are optional and may be non-sensitive. Add per-value encryption later if needed. Note: env_vars are never returned in API responses (same as `authorization_token` on resources).

## Track 4: Test Patterns

### Existing Test Structure

- `tests/test_api.py` — Core session/auth tests (16 tests)
- `tests/test_agents.py` — Agent CRUD/lifecycle (14 tests)
- `tests/test_resources.py` — GitHub repo resource validation + integration (11 tests)
- `tests/test_runtimes.py` — Runtime config tests
- `tests/conftest.py` — Shared fixtures

### Test Pattern for Environments

Follow the exact pattern of `test_agents.py`:

1. **CRUD tests**: create, create minimal, list, list excludes archived, get, get not found
2. **Validation tests**: invalid fields, missing fields, duplicate name
3. **Lifecycle tests**: archive, archive already archived, delete, delete with sessions (should fail)
4. **Integration tests**: create session with environment_id, create session inheriting agent's environment, wrapper script includes packages/env_vars/setup_script
5. **Version tests**: Update creates new version, version mismatch rejected

### Fixture Pattern

```python
@pytest.fixture
def environment(user):
    return Environment.objects.create(
        user=user,
        name="test-env",
        packages={"pip": ["pandas"], "apt": ["ffmpeg"]},
        env_vars={"DATABASE_URL": "postgres://..."},
        setup_script="echo setup",
        networking_type="unrestricted",
    )
```

## Code References

| File | Finding | Link |
|------|---------|------|
| `src/fairy/models.py:78-107` | AgentSession model — add environment FK here | — |
| `src/fairy/models.py:138-165` | Agent model — add optional environment FK here | — |
| `src/fairy/sprites_exec.py:50-83` | `build_wrapper_script` — inject packages/env_vars/setup_script here | — |
| `src/fairy/sprites_exec.py:16-47` | `_build_clone_section` — pattern for building script sections | — |
| `src/fairy/views.py:102-120` | `RunRequest` — add `environment_id` field | — |
| `src/fairy/views.py:131-240` | `create_session` — resolve environment and thread through | — |
| `src/fairy/views.py:464-510` | `_serialize_agent` / `_snapshot_version` — pattern for environment serialization | — |
| `src/fairy/runtimes.py:48-54` | `RuntimeConfig` — env_var field shows how runtime keys are mapped | — |
| `src/fairy/crypto.py` | `encrypt`/`decrypt` — available if env_vars need encryption | — |
| `src/fairy/signals.py:21-30` | Pre-delete signal pattern — reference for environment deletion guard | — |
| `src/fairy/urls.py:1-19` | URL patterns — add environment routes following same pattern | — |
| `tests/test_agents.py` | Test patterns for CRUD + lifecycle — mirror for environment tests | — |

## Proposed Model

```python
class Environment(models.Model):
    NETWORKING_CHOICES = [
        ("unrestricted", "Unrestricted"),
        ("limited", "Limited"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="environments")
    name = models.CharField(max_length=200)
    packages = models.JSONField(default=dict, blank=True)        # {"pip": [...], "apt": [...], ...}
    env_vars = models.JSONField(default=dict, blank=True)        # {"KEY": "value", ...}
    setup_script = models.TextField(blank=True, default="")      # Arbitrary bash
    networking_type = models.CharField(max_length=16, choices=NETWORKING_CHOICES, default="unrestricted")
    networking_config = models.JSONField(default=dict, blank=True)  # {"allowed_hosts": [...], ...}
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "environments"
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_user_environment_name"),
        ]
```

## Open Questions

1. **Should `env_vars` values be encrypted?** — Current recommendation is plaintext for v1, but secrets like DB passwords are common env vars.
2. **Should environments be versioned like agents?** — The Anthropic API says environments are not versioned, but fairy already has the AgentVersion pattern. Recommend: yes, add EnvironmentVersion for auditability.
3. **Networking enforcement** — How does Sprites handle network policies? If it doesn't, should fairy reject `limited` networking or accept-and-warn?
4. **Package version pinning validation** — Should fairy validate version specifiers (e.g., `pandas==2.2.0` for pip, `express@4.18.0` for npm), or just pass them through?
5. **Setup script security** — Arbitrary bash execution is powerful but risky. Should there be any sandboxing or validation beyond what Sprites provides?

## Related Research

- `thoughts/research/2026-04-16-github-repo-context.md` — How resources (GitHub repos) were added to sessions
- `thoughts/research/2026-04-16-session-based-execution.md` — Session model and streaming architecture
- `thoughts/research/2026-04-16-sprites-deep-dive.md` — Sprites container API capabilities
- `thoughts/plans/2026-04-16-session-based-execution.md` — Session-based execution plan (implemented)
