# Environment Model — Implementation Plan

## Overview

Add an Environment model to fairy that handles container configuration: package installation (apt, pip, npm, etc.), environment variables, setup scripts, and networking policy. Environments are reusable — create once, reference by ID across agents and sessions. This mirrors the Anthropic Managed Agents environment API with fairy-specific extensions (setup_script, env_vars).

## Research Summary

Research conducted directly (see `thoughts/research/2026-04-16-environment-model.md`):
- Wrapper script in `sprites_exec.py:50-83` has a clean insertion point for environment setup between git init and repo cloning
- Agent model already supports optional FKs and versioning patterns that Environment will mirror
- Existing test patterns in `test_agents.py` provide the template for environment CRUD tests
- Sprites containers run as root, so apt/pip/npm installs work directly
- Networking enforcement is not possible at the Sprites level — store config for forward compat

## Current State Analysis

**Current flow**: Sessions create a wrapper script with: API key export → prompt export → git init → repo cloning → exec agent. No mechanism for pre-installing packages, exporting custom env vars, or running setup commands.

**Existing models**: Agent (versioned, archivable), AgentSession (with resources), SessionResource (GitHub repos). No environment abstraction.

## Desired End State

```bash
# Create an environment
curl -X POST http://localhost:8777/environments \
  -H "Authorization: Bearer fairy_..." \
  -H "Content-Type: application/json" \
  -d '{
    "name": "data-science",
    "packages": {"apt": ["ffmpeg"], "pip": ["pandas", "numpy"]},
    "env_vars": {"DATABASE_URL": "postgres://..."},
    "setup_script": "createdb myapp",
    "networking": {"type": "unrestricted"}
  }'
# → 201 {"id": "env-uuid", "name": "data-science", ...}

# Use in a session
curl -X POST http://localhost:8777/sessions \
  -H "Authorization: Bearer fairy_..." \
  -H "Content-Type: application/json" \
  -d '{"runtime": "claude", "prompt": "analyze the data", "environment_id": "env-uuid"}'
# → 202 {"id": "session-uuid", "environment_id": "env-uuid", ...}

# Or attach to an agent as default
curl -X PUT http://localhost:8777/agents/agent-uuid \
  -H "Authorization: Bearer fairy_..." \
  -H "Content-Type: application/json" \
  -d '{"version": 1, "environment_id": "env-uuid"}'
```

Verify by:
1. `POST /environments` creates an environment with packages/env_vars/setup_script
2. `GET /environments` lists user's environments (excludes archived)
3. `POST /sessions` with `environment_id` produces a wrapper script that installs packages, exports env vars, and runs the setup script
4. Agent with `environment_id` passes its environment to sessions by default
5. Session's `environment_id` overrides agent's default
6. `POST /environments/{id}/archive` prevents new sessions but doesn't affect running ones
7. `DELETE /environments/{id}` only works if no sessions reference it
8. Existing tests still pass — fully backward compatible

## What We're NOT Doing

- No networking enforcement — Sprites doesn't expose network policies. Store the config, validate the shape, but don't inject iptables rules.
- No env_vars encryption — Store as plaintext JSON for v1. The values are never returned in API responses.
- No package version validation — Accept version specifiers as strings, pass them through to package managers.
- No setup_script sandboxing — Relies on Sprites container isolation.
- No environment sharing between users — Each environment is user-scoped.
- No environment "forking" or templating — Create from scratch each time.

## Implementation Approach

The Environment model follows the exact same patterns as Agent: UUID PK, user FK, versioned with optimistic concurrency, archivable. The wrapper script in `sprites_exec.py` gains three new sections (env vars, package installs, setup script) injected between git init and repo cloning. Session creation resolves the environment through: explicit `environment_id` > agent's `environment_id` > none.

## File Ownership Map

Single track (backend). All phases are sequential.

| File | Phase | Change Type |
|------|-------|-------------|
| `src/fairy/models.py` | 1 | modify — add Environment, EnvironmentVersion models; add FKs to Agent/AgentSession |
| `src/fairy/migrations/0007_*.py` | 1 | create (generated) |
| `src/fairy/sprites_exec.py` | 2 | modify — add env vars, packages, setup script sections to wrapper |
| `src/fairy/views.py` | 3 | modify — add environment CRUD views; update session creation to resolve environment |
| `src/fairy/urls.py` | 3 | modify — add environment routes |
| `src/fairy/admin.py` | 4 | modify — register Environment models |
| `tests/test_environments.py` | 4 | create — environment CRUD, validation, lifecycle, integration tests |
| `tests/test_resources.py` | 4 | modify — update `build_wrapper_script` tests for environment param |

---

## Phase 1: Models & Migration

### Overview
Add Environment and EnvironmentVersion models. Add optional `environment` FK to Agent and AgentSession.

### Changes Required:

#### 1. `src/fairy/models.py` — Add Environment models

Add after the `SessionResource` class, before `Agent`:

```python
class Environment(models.Model):
    NETWORKING_CHOICES = [
        ("unrestricted", "Unrestricted"),
        ("limited", "Limited"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="environments"
    )
    name = models.CharField(max_length=200)
    packages = models.JSONField(default=dict, blank=True)
    env_vars = models.JSONField(default=dict, blank=True)
    setup_script = models.TextField(blank=True, default="")
    networking_type = models.CharField(
        max_length=16, choices=NETWORKING_CHOICES, default="unrestricted"
    )
    networking_config = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "environments"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"],
                condition=models.Q(archived_at__isnull=True),
                name="unique_active_environment_name",
            ),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.id})"

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class EnvironmentVersion(models.Model):
    environment = models.ForeignKey(
        Environment, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    packages = models.JSONField(default=dict, blank=True)
    env_vars = models.JSONField(default=dict, blank=True)
    setup_script = models.TextField(blank=True, default="")
    networking_type = models.CharField(max_length=16)
    networking_config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "environment_versions"
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "version"], name="unique_environment_version"
            ),
        ]
        ordering = ["-version"]

    def __str__(self):
        return f"{self.environment.name} v{self.version}"
```

**Design notes**:
- `packages` is `{"pip": ["pandas", "numpy==2.2.0"], "apt": ["ffmpeg"], "npm": ["express@4.18"]}` — keys are package manager names, values are string arrays. Unknown keys are ignored (forward compat).
- `env_vars` is `{"KEY": "value"}` — flat string→string dict. Never returned in API responses.
- Unique constraint is conditional: only active (non-archived) environments must have unique names per user. This allows re-creating an environment with the same name after archiving the old one.
- EnvironmentVersion mirrors AgentVersion exactly.

#### 2. Add FK to Agent model

```python
# In Agent class, add field:
environment = models.ForeignKey(
    "Environment", on_delete=models.SET_NULL, null=True, blank=True, related_name="agents"
)
```

Also add `"environment_id"` to `AGENT_VERSIONED_FIELDS` in `views.py`.

#### 3. Add FK to AgentSession model

```python
# In AgentSession class, add field:
environment = models.ForeignKey(
    "Environment", on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions"
)
```

`SET_NULL` on both: deleting an environment shouldn't cascade to agents or sessions.

#### 4. Generate migration

```bash
uv run python manage.py makemigrations fairy
```

### Success Criteria:

#### Automated Verification:
- [ ] `uv run python manage.py makemigrations --check` reports no pending migrations
- [ ] `uv run python manage.py migrate` succeeds
- [ ] `uv run ruff check src/` passes
- [ ] `uv run pytest tests/ -v` — all existing tests pass

#### Manual Verification:
- [ ] `uv run python manage.py shell -c "from fairy.models import Environment, EnvironmentVersion; print('OK')"`

**Gate**: Migration applies cleanly, all existing tests pass.

---

## Phase 2: Wrapper Script Changes

### Overview
Modify `sprites_exec.py` to accept an environment config and inject package installation, env var exports, and setup script into the wrapper.

### Changes Required:

#### 1. `src/fairy/sprites_exec.py` — Add environment setup sections

Add a new dataclass and builder functions:

```python
@dataclass(frozen=True)
class EnvironmentSetup:
    """Container environment configuration extracted from an Environment model."""
    packages: dict[str, list[str]]  # {"pip": [...], "apt": [...], ...}
    env_vars: dict[str, str]        # {"KEY": "value", ...}
    setup_script: str               # Arbitrary bash commands
```

Add three new section builders:

```python
PACKAGE_MANAGER_ORDER = ["apt", "cargo", "gem", "go", "npm", "pip"]

def _build_env_vars_section(env_vars: dict[str, str]) -> str:
    """Build export statements for environment variables."""
    if not env_vars:
        return ""
    lines = ["# Environment variables"]
    for key, value in sorted(env_vars.items()):
        lines.append(f"export {shlex.quote(key)}={shlex.quote(value)}")
    return "\n".join(lines)


def _build_packages_section(packages: dict[str, list[str]]) -> str:
    """Build package installation commands in alphabetical manager order."""
    if not packages:
        return ""
    lines = ["# Install packages"]
    for manager in PACKAGE_MANAGER_ORDER:
        pkgs = packages.get(manager, [])
        if not pkgs:
            continue
        quoted = " ".join(shlex.quote(p) for p in pkgs)
        if manager == "apt":
            lines.append(f"apt-get update -qq && apt-get install -y -qq {quoted}")
        elif manager == "pip":
            lines.append(f"pip install --quiet {quoted}")
        elif manager == "npm":
            lines.append(f"npm install --global --silent {quoted}")
        elif manager == "cargo":
            for pkg in pkgs:
                lines.append(f"cargo install {shlex.quote(pkg)}")
        elif manager == "gem":
            lines.append(f"gem install --silent {quoted}")
        elif manager == "go":
            for pkg in pkgs:
                lines.append(f"go install {shlex.quote(pkg)}")
    return "\n".join(lines)


def _build_setup_script_section(setup_script: str) -> str:
    """Build the custom setup script section."""
    if not setup_script.strip():
        return ""
    return f"# Custom setup\n{setup_script}"
```

#### 2. Update `build_wrapper_script` signature and body

```python
def build_wrapper_script(
    config: RuntimeConfig,
    api_key: str,
    prompt: str,
    *,
    continue_session: bool = False,
    repos: list[RepoSpec] | None = None,
    environment: EnvironmentSetup | None = None,
) -> str:
    cmd = config.continue_cmd if continue_session else config.cmd
    clone_section = _build_clone_section(repos or [])

    env_vars_section = ""
    packages_section = ""
    setup_section = ""
    if environment:
        env_vars_section = _build_env_vars_section(environment.env_vars)
        packages_section = _build_packages_section(environment.packages)
        setup_section = _build_setup_script_section(environment.setup_script)

    return f"""#!/bin/bash
set -euo pipefail
export {config.env_var}={shlex.quote(api_key)}
export PROMPT={shlex.quote(prompt)}
{env_vars_section}

# Setup working directory
cd /home/sprite
mkdir -p .gemini
if [ ! -d .git ]; then
    git init -q
    git add -A 2>/dev/null || true
    git commit -q -m "init" --allow-empty 2>/dev/null || true
fi

{packages_section}

{clone_section}

{setup_section}

exec {cmd}
"""
```

**Key decisions**:
- Env vars are exported at the top (available to all subsequent commands including package installs)
- Packages install after git init but before repo cloning (repos may depend on installed tools)
- Setup script runs last before exec (can reference installed packages and cloned repos)
- `shlex.quote` on all env var keys and values for safety
- Package manager order is alphabetical (matching Anthropic docs): apt, cargo, gem, go, npm, pip

### Success Criteria:

#### Automated Verification:
- [ ] `uv run ruff check src/fairy/sprites_exec.py` passes
- [ ] `uv run pytest tests/ -v` — existing wrapper script tests still pass (they don't pass `environment=`)
- [ ] `uv run python -c "from fairy.sprites_exec import EnvironmentSetup, build_wrapper_script; print('OK')"`

#### Manual Verification:
- [ ] Build a script with environment and verify the output contains all sections in correct order:
  ```python
  from fairy.runtimes import RUNTIMES
  from fairy.sprites_exec import EnvironmentSetup, build_wrapper_script
  env = EnvironmentSetup(
      packages={"pip": ["pandas"], "apt": ["ffmpeg"]},
      env_vars={"DB_URL": "postgres://localhost/db"},
      setup_script="echo 'ready'",
  )
  print(build_wrapper_script(RUNTIMES["claude"], "sk-test", "hello", environment=env))
  ```

**Gate**: Wrapper script output is correct, all existing tests pass.

---

## Phase 3: Views & URLs

### Overview
Add environment CRUD endpoints. Update session creation to resolve environment. Update agent serialization for environment_id.

### Changes Required:

#### 1. `src/fairy/views.py` — Add Pydantic models

```python
VALID_PACKAGE_MANAGERS = {"apt", "cargo", "gem", "go", "npm", "pip"}


class CreateEnvironmentRequest(BaseModel):
    name: str = Field(max_length=200)
    packages: dict[str, list[str]] = Field(default_factory=dict)
    env_vars: dict[str, str] = Field(default_factory=dict)
    setup_script: str = Field(default="")
    networking: dict = Field(default_factory=lambda: {"type": "unrestricted"})

    @field_validator("packages")
    @classmethod
    def validate_packages(cls, v: dict) -> dict:
        for manager, pkgs in v.items():
            if manager not in VALID_PACKAGE_MANAGERS:
                raise ValueError(
                    f"Unknown package manager: {manager}. "
                    f"Must be one of: {sorted(VALID_PACKAGE_MANAGERS)}"
                )
            if not isinstance(pkgs, list) or not all(isinstance(p, str) for p in pkgs):
                raise ValueError(f"packages.{manager} must be a list of strings")
        return v

    @field_validator("networking")
    @classmethod
    def validate_networking(cls, v: dict) -> dict:
        net_type = v.get("type", "unrestricted")
        if net_type not in ("unrestricted", "limited"):
            raise ValueError("networking.type must be 'unrestricted' or 'limited'")
        if net_type == "limited":
            hosts = v.get("allowed_hosts", [])
            if not isinstance(hosts, list):
                raise ValueError("networking.allowed_hosts must be a list")
        return v


class UpdateEnvironmentRequest(BaseModel):
    version: int = Field(description="Current version — optimistic concurrency check")
    name: str | None = None
    packages: dict[str, list[str]] | None = None
    env_vars: dict[str, str] | None = None
    setup_script: str | None = None
    networking: dict | None = None

    @field_validator("packages")
    @classmethod
    def validate_packages(cls, v: dict | None) -> dict | None:
        if v is not None:
            for manager in v:
                if manager not in VALID_PACKAGE_MANAGERS:
                    raise ValueError(f"Unknown package manager: {manager}")
        return v
```

#### 2. `src/fairy/views.py` — Add environment views

Follow the exact pattern of agent views:

- `environments_list_create` — POST: create, GET: list (excludes archived)
- `environment_detail` — GET: retrieve, PUT: update (with version check)
- `environment_archive` — POST: archive
- `environment_delete` — DELETE: only if no sessions reference it
- `environment_versions` — GET: list versions

Serialization:

```python
def _serialize_environment(env: Environment) -> dict:
    return {
        "id": str(env.id),
        "type": "environment",
        "name": env.name,
        "packages": env.packages,
        # env_vars intentionally omitted — contains secrets
        "setup_script": env.setup_script or None,
        "networking": {
            "type": env.networking_type,
            **(env.networking_config if env.networking_type == "limited" else {}),
        },
        "version": env.version,
        "created_at": env.created_at.isoformat(),
        "updated_at": env.updated_at.isoformat(),
        "archived_at": env.archived_at.isoformat() if env.archived_at else None,
    }
```

**Important**: `env_vars` is NOT included in serialization responses. Callers can set them but never read them back (same pattern as `authorization_token` on resources).

#### 3. Update session creation

In `create_session`, after resolving the agent:

```python
# Resolve environment: explicit > agent > none
environment_obj = None
env_id = req.environment_id or (agent_obj.environment_id if agent_obj else None)
if env_id:
    try:
        environment_obj = Environment.objects.get(pk=env_id, user=request.user)
    except (Environment.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Environment not found"}, status=404)
    if environment_obj.is_archived:
        return JsonResponse(
            {"detail": "Cannot create session with archived environment"}, status=409
        )
```

Then pass to `build_wrapper_script`:

```python
from fairy.sprites_exec import EnvironmentSetup

env_setup = None
if environment_obj:
    env_setup = EnvironmentSetup(
        packages=environment_obj.packages,
        env_vars=environment_obj.env_vars,
        setup_script=environment_obj.setup_script,
    )

script = build_wrapper_script(
    config, api_key, effective_prompt, repos=repo_specs, environment=env_setup
)
```

Save on session:

```python
session = AgentSession.objects.create(
    user=request.user,
    agent=agent_obj,
    environment=environment_obj,  # NEW
    runtime=runtime,
    prompt=req.prompt,
    sprite_name=name,
    status="pending",
)
```

#### 4. Update `RunRequest`

```python
class RunRequest(BaseModel):
    runtime: str | None = Field(default=None)
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600)
    agent_id: str | None = Field(default=None)
    environment_id: str | None = Field(default=None)  # NEW
    resources: list[GitHubRepoResource] = Field(default_factory=list)
```

#### 5. Update agent serialization

Add `environment_id` to `_serialize_agent` and `_serialize_agent_version`. Add to `AGENT_VERSIONED_FIELDS`.

#### 6. Update session serialization

Add `environment_id` to `get_session` response.

#### 7. `src/fairy/urls.py` — Add routes

```python
# Environments
path("environments", views.environments_list_create),
path("environments/<uuid:environment_id>", views.environment_detail),
path("environments/<uuid:environment_id>/archive", views.environment_archive),
path("environments/<uuid:environment_id>/delete", views.environment_delete),
path("environments/<uuid:environment_id>/versions", views.environment_versions),
```

### Success Criteria:

#### Automated Verification:
- [ ] `uv run ruff check src/` passes
- [ ] `uv run pytest tests/ -v` — all existing tests pass

#### Manual Verification:
- [ ] Create an environment via API
- [ ] Create a session with `environment_id` — verify wrapper script includes packages/env_vars/setup_script
- [ ] Create an agent with `environment_id`, then a session from that agent — environment inherited
- [ ] Override agent's environment at session creation
- [ ] Archive an environment, verify new sessions are rejected
- [ ] Delete an environment with no sessions — succeeds
- [ ] Delete an environment with sessions — fails

**Gate**: Full CRUD works, session integration verified, all existing tests pass.

---

## Phase 4: Admin & Tests

### Overview
Register environment models in admin. Write comprehensive tests.

### Changes Required:

#### 1. `src/fairy/admin.py` — Register Environment

```python
@admin.register(Environment)
class EnvironmentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "networking_type", "version", "created_at")
    list_filter = ("networking_type",)
    search_fields = ("name", "id")
    readonly_fields = ("id", "created_at", "updated_at")
```

#### 2. `tests/test_environments.py` — New test file

Test categories:

**CRUD (mirrors test_agents.py)**:
- `test_create_environment` — full payload
- `test_create_environment_minimal` — just name
- `test_create_environment_invalid_package_manager` — unknown manager rejected
- `test_create_environment_invalid_networking_type` — bad type rejected
- `test_list_environments` — returns user's active environments
- `test_list_environments_excludes_archived`
- `test_list_environments_other_user_not_visible`
- `test_get_environment`
- `test_get_environment_not_found`
- `test_get_environment_omits_env_vars` — response never includes env_vars
- `test_update_environment`
- `test_update_environment_version_mismatch`
- `test_update_archived_environment`

**Lifecycle**:
- `test_archive_environment`
- `test_archive_already_archived`
- `test_delete_environment_no_sessions`
- `test_delete_environment_with_sessions_rejected`

**Versioning**:
- `test_list_environment_versions`

**Session integration**:
- `test_create_session_with_environment`
- `test_create_session_inherits_agent_environment`
- `test_create_session_explicit_environment_overrides_agent`
- `test_create_session_with_archived_environment_rejected`
- `test_get_session_includes_environment_id`

#### 3. `tests/test_resources.py` — Update wrapper script tests

Add tests for `build_wrapper_script` with `environment` parameter:

- `test_wrapper_script_with_packages` — apt/pip/npm commands present in correct order
- `test_wrapper_script_with_env_vars` — export statements present
- `test_wrapper_script_with_setup_script` — custom script present after packages
- `test_wrapper_script_environment_order` — env vars before packages before clone before setup before exec
- `test_wrapper_script_no_environment` — backward compat, same as before

### Success Criteria:

#### Automated Verification:
- [ ] `uv run ruff check src/ tests/` passes
- [ ] `uv run pytest tests/ -v` — all tests pass (old + new)

---

## Testing Strategy

### Automated:
- Environment CRUD validation (invalid JSON, missing fields, bad package manager, bad networking) — Pydantic returns 422
- Environment lifecycle (create, update, archive, delete) — DB-level tests
- Session+environment integration — mock Sprites, verify wrapper script content
- Wrapper script section ordering — unit tests on `build_wrapper_script`

### Manual Testing Steps:
1. Create an environment with packages: `{"apt": ["curl"], "pip": ["requests"]}`
2. Create a session with that environment
3. Stream logs and verify packages were installed before agent started
4. Create an agent with the environment, then a session from the agent
5. Override the environment at session creation time
6. Archive the environment, try to create a new session — should fail
7. Verify env_vars are never in GET responses

## Performance Considerations

- **Package installation time**: apt/pip/npm installs add startup latency. For frequently used environments, consider caching (future optimization — not v1).
- **No DB impact**: Environment is a low-cardinality table (tens to hundreds of rows). No indexing concerns.
- **Wrapper script size**: With large package lists and setup scripts, the script file grows. Sprites filesystem write should handle this fine.

## References

- Research: `thoughts/research/2026-04-16-environment-model.md`
- Anthropic Managed Agents Environments docs (provided by user)
- Agent model patterns: `src/fairy/models.py:138-188`, `src/fairy/views.py:420-654`
- Wrapper script: `src/fairy/sprites_exec.py:50-83`
- Session creation: `src/fairy/views.py:131-240`
