---
date: 2026-04-21
researcher: Claude Code (synthesis + redesign)
git_commit: 919a4d4fd679effee2099161911a3146aedc6a64
branch: plans/provision-stage-events
repository: ravi-hq/agent-on-demand
topic: "Meta-runtime redesign: Runtime-as-protocol, provider-keyed credentials, all-creds-on-Sprite"
tags: [research, redesign, runtimes, meta-runtime, goose, opencode, breaking-change]
status: complete
method: synthesis-and-redesign
sources:
  - thoughts/research/2026-04-21-goose-runtime-support.md
  - thoughts/research/2026-04-21-opencode-runtime-support.md
last_updated: 2026-04-21
last_updated_by: Claude Code
note: Supersedes the back-compat-preserving synthesis. Assumes no API stability or data preservation requirements.
---

# Research: Runtime redesign — provider-keyed credentials, Runtime-as-protocol, all-creds-on-Sprite

**Date**: 2026-04-21
**Researcher**: Claude Code
**Git Commit**: [`919a4d4`](https://github.com/ravi-hq/agent-on-demand/commit/919a4d4fd679effee2099161911a3146aedc6a64)
**Method**: Synthesis of [`goose`](2026-04-21-goose-runtime-support.md) and [`opencode`](2026-04-21-opencode-runtime-support.md) research, followed by ground-up redesign given no back-compat or data-preservation constraints.

## Research Question

The goose and opencode research efforts independently surfaced the same friction: fairy's `RuntimeConfig` is shaped for *pinned-provider* CLIs (claude → Anthropic, codex → OpenAI, gemini → Google), and *unpinned* CLIs (goose, opencode, future entrants) keep generating workarounds — env var aliases, model-string disambiguation prefixes, a `MODEL_RUNTIME_MAP` that nothing actually enforces, a `test_each_runtime_has_unique_env_var` invariant that no longer matches reality.

Given (a) the existing approach works but won't scale to the next N runtimes, and (b) we have no back-compat or data preservation constraints, what's the right shape going forward?

## Summary

Stop patching. Rotate the data model around `(provider, model)` instead of `(runtime)`, make each `Runtime` a Python class implementing a small protocol, and dump *all* of a user's credentials onto every Sprite at provision time so the env-var-selection problem dissolves entirely.

Concretely:

1. **`Runtime` becomes a protocol/class**, one Python file per runtime. Owns `build_command`, `write_config`, `providers`, `skills_root`. Replaces the 4-field `RuntimeConfig` dataclass + scattered `_write_mcp_<runtime>` / `_SKILLS_ROOTS[runtime]` / shell-template `cmd`/`continue_cmd` indirection.
2. **`UserCredential(user, kind, value_encrypted)`** replaces `UserRuntimeKey`. `kind` enumerates `provider:anthropic`, `provider:openai`, `runtime_token:claude-oauth`, etc. — one model handles both provider keys and runtime-specific auth tokens.
3. **All user credentials get written to every Sprite.** `_write_env_file` becomes a flat dump: every `UserCredential` the user owns, mapped to its conventional env var name. No per-runtime env var selection, no aliasing, no translation.
4. **Model is a free-form `provider/model_id` string** validated against a small `MODELS` registry. Replaces the `AgentModel` enum. Adding a model is one registry entry, no migration.
5. **`MODEL_RUNTIME_MAP` becomes real enforcement.** Each `Runtime` declares `providers: set[str]`. Agent-create validates `model.provider ∈ runtime.providers`. Replaces the documentation-only map that nothing currently calls.
6. **`claude-oauth` folds into `ClaudeRuntime`.** It's not a separate runtime — it's claude with a different credential. The runtime picks `CLAUDE_CODE_OAUTH_TOKEN` over `ANTHROPIC_API_KEY` based on which credential the user has. One fewer entry in `RUNTIMES`.

The shell-template `bash -c "set -a; source ..."` indirection goes away. `_write_env_file` shrinks to ~10 lines. `MODEL_RUNTIME_MAP`, the `AgentModel` enum, the env var alias plumbing, the unique-env-var test, the `goose/<provider>/<model>` disambiguation prefix, and the `claude-oauth` runtime entry all disappear.

## What the source docs established

The synthesis from goose + opencode research confirmed several findings that survive the redesign and motivate it:

- **`MODEL_RUNTIME_MAP` is documentation, not enforcement.** Never imported anywhere outside its declaration; agent-create takes `runtime` as an independent client field. The redesign makes it real.
- **Stream is byte passthrough.** `stream.py` doesn't care what runtime emitted what — survives unchanged.
- **Per-runtime config writers are the right seam.** Codex already does this implicitly (`_write_mcp_codex` writes the whole `config.toml`). The redesign generalizes: every runtime owns its `write_config`.
- **Sprite base image is external and the same problem twice.** Both goose and opencode need binary pre-installation outside this repo. One coordinated base-image PR.
- **Both meta-runtimes want one runtime entry, not one-per-provider.** Multi-provider runtimes are first-class in the redesign rather than a special case.
- **Migrations are cosmetic in the existing schema.** No DB CHECK constraints; choices are app-level. Recreating tables is cheap.
- **`test_each_runtime_has_unique_env_var` is the canary.** Both runtimes hit it. The redesign deletes it (no longer meaningful — env vars come from credentials, not runtimes).

The goose/opencode docs each proposed a *workaround* for the env-var collision (alias translation, test relaxation). The redesign eliminates the underlying mismatch.

## The new shape

### `Runtime` protocol

```python
# src/agent_on_demand/runtimes/base.py
from typing import Protocol, Literal
from sprites import Sprite
from agent_on_demand.session_service.specs import SessionSpec, McpServerSpec


class Runtime(Protocol):
    name: str
    providers: set[str]            # which providers this runtime can serve
    skills_root: str | None        # absolute path on Sprite, or None to disable skills

    def build_command(
        self, spec: SessionSpec, mode: Literal["run", "continue"]
    ) -> list[str]:
        """Argv for the per-turn command. Receives prompt via stdin."""

    def write_config(
        self, sprite: Sprite, spec: SessionSpec, mcp_servers: list[McpServerSpec]
    ) -> None:
        """Write any per-runtime config files on the Sprite (config.yaml, MCP, etc).
        Called once at provision time, even if mcp_servers is empty."""
```

Implementations live in `src/agent_on_demand/runtimes/`:

- `claude.py` → `ClaudeRuntime` — `providers={"anthropic"}`, picks token vs. API key based on user credentials, writes `~/.claude.json` for MCP, `skills_root="/home/sprite/.claude/skills"`
- `codex.py` → `CodexRuntime` — `providers={"openai"}`, writes `~/.codex/config.toml`, `skills_root="/home/sprite/.codex/skills"`
- `gemini.py` → `GeminiRuntime` — `providers={"google"}`, writes `~/.gemini/settings.json`, `skills_root="/home/sprite/.gemini/skills"`
- `goose.py` → `GooseRuntime` — `providers={"anthropic", "openai", "google", ...}`, writes `~/.config/goose/config.yaml` (provider/model/mode/keyring/extensions in one file), `skills_root=None` (recipes incompatible with SKILL.md)
- `opencode.py` → `OpencodeRuntime` — `providers={"anthropic", "openai", "google", "azure", "bedrock", ...}`, writes `~/.config/opencode/opencode.json`, `skills_root="/home/sprite/.config/opencode/skills"` (free SKILL.md compatibility)

`RUNTIMES: dict[str, Runtime]` becomes a registry of class instances. The runtime's quirks (config file format, MCP serialization, resume flag, skills convention) all live in one file, testable in isolation.

### Command building moves to Python

The shell-template `cmd: str = '... -p "$PROMPT"'` indirection goes away. Today's flow:

1. `RuntimeConfig.cmd` is a shell string with `$PROMPT`/`$AOD_SESSION_ID` placeholders
2. `build_turn_command` wraps it in `bash -c "set -a; source /tmp/aod-env; set +a; PROMPT=$(cat); export PROMPT; exec ${cmd}"`
3. Sprite executes the wrapped string

New flow:

1. `runtime.build_command(spec, mode)` returns `list[str]` — Python builds it
2. Sprite executes the argv directly (`sprite.command(*argv, stdin=prompt_bytes)`)

The wrapper script disappears. Quoting handled by sprite SDK. The prompt arrives via stdin (already supported) instead of via a `$PROMPT` env var. `build_command` is a pure function, trivially unit-testable per runtime.

The env file is *still* sourced by the Sprite (via `bash -lc 'source /tmp/aod-env; exec "$@"' --` or equivalent — the sprite SDK pattern), but its contents are flat key=value with no template substitution semantics.

### `UserCredential` model

```python
# src/agent_on_demand/models/auth.py
class UserCredential(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    kind = models.CharField(max_length=64)  # "provider:anthropic", "runtime_token:claude-oauth"
    value_encrypted = ...                   # same EncryptedField pattern as today
    created_at = ...
    updated_at = ...

    class Meta:
        unique_together = [("user", "kind")]
```

Replaces `UserRuntimeKey`. Drop the old table; recreate. The `kind` field is a structured string with two namespaces:

- `provider:<name>` — provider API keys: `provider:anthropic`, `provider:openai`, `provider:google`, `provider:azure`, `provider:bedrock`, etc.
- `runtime_token:<runtime>` — runtime-specific auth tokens that aren't provider keys: `runtime_token:claude-oauth`. This is rare; most runtimes use provider keys.

A small registry maps kinds to env var names:

```python
CREDENTIAL_ENV_VAR: dict[str, str] = {
    "provider:anthropic":         "ANTHROPIC_API_KEY",
    "provider:openai":            "OPENAI_API_KEY",
    "provider:google":            "GEMINI_API_KEY",
    "provider:azure":             "AZURE_API_KEY",
    "provider:bedrock":           "AWS_BEARER_TOKEN_BEDROCK",
    "runtime_token:claude-oauth": "CLAUDE_CODE_OAUTH_TOKEN",
    # ...
}
```

### All credentials on every Sprite

`_write_env_file` becomes ~10 lines:

```python
def _write_env_file(sprite: Sprite, spec: SessionSpec) -> None:
    lines: list[str] = []
    for cred in UserCredential.objects.filter(user=spec.user):
        env_name = CREDENTIAL_ENV_VAR.get(cred.kind)
        if env_name:
            lines.append(f"{env_name}={shlex.quote(cred.value)}")
    if spec.runtime_session_id:
        lines.append(f"AOD_SESSION_ID={shlex.quote(spec.runtime_session_id)}")
    if spec.model:
        lines.append(f"AOD_MODEL={shlex.quote(spec.model)}")
    for k, v in sorted((spec.environment.env_vars or {}).items()) if spec.environment else []:
        lines.append(f"{k}={shlex.quote(v)}")
    fs = sprite.filesystem()
    (fs / ENV_FILE_PATH.lstrip("/")).write_text("\n".join(lines) + "\n")
    sprite.command("chmod", "600", ENV_FILE_PATH).run()
```

No per-runtime env var selection. No alias translation. No `META_RUNTIMES` set. Every credential the user has registered shows up under its conventional env var name. The runtime CLI reads what it reads.

**Trade-off (explicit decision)**: a compromised Sprite leaks the user's full credential set rather than the single key for the active runtime. Three things make this acceptable:

1. **Sprites are short-lived per-session** — a compromise has bounded lifetime.
2. **The marginal blast radius is proportional, not categorical** — if a Sprite is owned, *any* key on it leaks today; "all keys leak vs. one key leaks" is degree, not kind.
3. **The user is authorizing the session** — the credentials in question are the user's own, used on their behalf.

If a user has high-value credentials they don't want their own agents to be able to leak, those credentials don't belong in fairy. Codifying a "session-scoped credential" opt-out is **explicitly out of scope for v1** — revisit only if a real customer asks.

### Model registry

```python
# src/agent_on_demand/models_registry.py (or wherever)
@dataclass(frozen=True)
class ModelDef:
    id: str          # canonical "provider/model_id" string
    provider: str    # "anthropic"
    runtimes: set[str] | None = None  # optional; None means "any runtime whose providers includes this provider"

MODELS: dict[str, ModelDef] = {
    "anthropic/claude-sonnet-4-5":   ModelDef(id="anthropic/claude-sonnet-4-5", provider="anthropic"),
    "anthropic/claude-haiku-4-5":    ModelDef(id="anthropic/claude-haiku-4-5",  provider="anthropic"),
    "anthropic/claude-opus-4-7":     ModelDef(id="anthropic/claude-opus-4-7",   provider="anthropic"),
    "openai/gpt-5":                  ModelDef(id="openai/gpt-5",                provider="openai"),
    "openai/o3":                     ModelDef(id="openai/o3",                   provider="openai"),
    "google/gemini-2.5-pro":         ModelDef(id="google/gemini-2.5-pro",       provider="google"),
    # ...
}
```

`AgentModel` enum is gone. `MODEL_RUNTIME_MAP` is gone. Adding a model is one registry entry, no migration.

Validation at `POST /agents`:

```python
model_def = MODELS.get(req.model)
if not model_def:
    return error(422, f"Unknown model: {req.model}")

runtime = RUNTIMES.get(req.runtime)
if not runtime:
    return error(422, f"Unknown runtime: {req.runtime}")

if model_def.provider not in runtime.providers:
    return error(422, f"Runtime {req.runtime} cannot serve model {req.model} (provider {model_def.provider} not in {runtime.providers})")

if model_def.runtimes is not None and req.runtime not in model_def.runtimes:
    return error(422, f"Model {req.model} not supported on runtime {req.runtime}")
```

This is the enforcement that `MODEL_RUNTIME_MAP` was supposed to provide. Today it doesn't exist; tomorrow it does.

### `claude-oauth` dissolves

`ClaudeRuntime.build_command` checks the user's credentials:

```python
def build_command(self, spec, mode):
    if spec.user_has_credential("runtime_token:claude-oauth"):
        # Use OAuth flow
        return ["claude", "--dangerously-skip-permissions", "--print", ...] if mode == "continue" else [...]
    else:
        # Use API key flow
        return ["claude", "--print", "--verbose", "--output-format", "stream-json", "--session-id", spec.runtime_session_id, ...]
```

The two runtime entries collapse into one. The user's credential set determines which auth path runs. One fewer `RUNTIMES` entry, one fewer `Runtime` class, one fewer test fixture.

### Schema changes

| Today | Tomorrow |
|-------|----------|
| `UserRuntimeKey(user, runtime)` table | `UserCredential(user, kind)` table |
| `AgentModel` Python enum | Deleted |
| `MODEL_RUNTIME_MAP` Python dict | Deleted |
| `Agent.model` `CharField(choices=AgentModel.choices())` | `Agent.model` `CharField` (free-form, validated against MODELS at create) |
| `Agent.runtime` `CharField(choices=RUNTIMES_CHOICES)` | `Agent.runtime` `CharField` (free-form, validated against RUNTIMES at create) |
| `AgentSession.runtime` denormalized `CharField` | Same — keep for query convenience |
| `AgentVersion.{model,runtime}` denormalized | Same — keep for version history correctness |
| `RuntimeConfig` 4-field dataclass | `Runtime` Protocol with N implementations |
| `_write_mcp_<runtime>` per-runtime functions | `Runtime.write_config` method per class |
| `_SKILLS_ROOTS` dict | `Runtime.skills_root` attribute |
| `RUNTIMES: dict[str, RuntimeConfig]` | `RUNTIMES: dict[str, Runtime]` |
| `tests/test_runtimes.py::test_each_runtime_has_unique_env_var` | Deleted |

No data migration — drop and recreate.

## What this means for goose and opencode

Both become drop-in `Runtime` implementations. No special-casing in `_write_env_file`, no env var alias translation, no `META_RUNTIMES` set, no model-string disambiguation prefix.

**`GooseRuntime`** (~80 lines):
- `providers = {"anthropic", "openai", "google", "azure", "bedrock"}` (start with anthropic + openai; add as demanded)
- `skills_root = None` (recipes incompatible with SKILL.md)
- `build_command` returns argv with `--text`, `--output-format stream-json`, `--mode auto`, `--name <session_id>`, `--provider <derived>`, `--model <derived>`, `--resume` for continue mode. Provider/model derived in Python from `spec.model.split("/", 1)`.
- `write_config` writes `~/.config/goose/config.yaml` with provider/model/mode/keyring/telemetry + `extensions:` block (MCP)

**`OpencodeRuntime`** (~80 lines):
- `providers = {"anthropic", "openai", "google", "azure", "bedrock", ...}` (15-75 supported; pick the ones you want to expose)
- `skills_root = "/home/sprite/.config/opencode/skills"` (native SKILL.md — free)
- `build_command` returns `["opencode", "run", "--model", spec.model, "--format", "json", spec.prompt]` for run mode, `["opencode", "run", "--model", spec.model, "--format", "json", "--continue", spec.prompt]` for continue. Bare `provider/model_id` passed verbatim — no prefix games because the model string IS what opencode wants.
- `write_config` writes `~/.config/opencode/opencode.json` with `mcp` block

Both runtimes pick up their provider's API key from the env file because `_write_env_file` already dumped every credential the user owns.

## What stays unchanged

- `stream.py` (byte passthrough)
- `Sprite` SDK usage (`sprite.command`, `sprite.filesystem`)
- `_install_packages`, `_clone_repos`, `_apply_network_policy`, `_run_user_setup` provisioning stages
- `Environment` model and its `env_vars` merging behavior
- `provision_session` overall stage flow and failure handling
- `tasks.py` outer structure (Procrastinate task, log buffering, exit code → status mapping)
- Sprite base image dependency (still external — coordinate one PR for goose + opencode + anything else)

## Cost

Bigger PR than either previous plan, but everything internal — no API breakage to defend.

| Change | Volume |
|--------|--------|
| Replace `RuntimeConfig` dataclass + `RUNTIMES` dict | 1 protocol file + 5 runtime class files (~50-80 lines each) |
| Replace `AgentModel` enum + `MODEL_RUNTIME_MAP` | 1 dataclass + 1 dict (~50 lines) |
| Replace `UserRuntimeKey` model | 1 Django model (~30 lines) + drop+recreate migration |
| Rewrite `_write_env_file` | Net shrink: -15 lines |
| Rewrite `build_turn_command` and tasks.py command path | Net shrink: -20 lines (no shell template wrapper) |
| Rewrite `_write_mcp_config` dispatch + per-runtime writers | Net shrink: existing functions move into runtime classes |
| Rewrite agent-create validation | Net add: ~20 lines for proper model+runtime+provider check |
| Rewrite tests | Significant — each runtime class gets its own test file; existing `test_runtimes.py`/`test_tools_mcp.py`/`test_skills.py` rewritten or replaced |

Net code volume probably similar or smaller. Conceptual complexity meaningfully lower.

## Implementation order

1. **Land the redesign infrastructure first.** Runtime protocol, `UserCredential` model, MODELS registry, rewrite `_write_env_file`, rewrite the command-build path in `tasks.py`, port existing claude/codex/gemini to `Runtime` classes, fold `claude-oauth` into `ClaudeRuntime`. Drop `UserRuntimeKey`, `AgentModel`, `MODEL_RUNTIME_MAP`. Delete `test_each_runtime_has_unique_env_var` and the rest of the now-obsolete test scaffolding. Rewrite the tests around the new shape.

2. **Drop+recreate the relevant tables.** No data preservation. Squash migrations or just generate a clean reset for the affected models.

3. **Land `OpencodeRuntime`.** First user of the new infrastructure end-to-end; gets `SKILL.md` reuse essentially free.

4. **Land `GooseRuntime`.** Almost trivial at this point — write the class, register it, ship tests.

5. **Coordinate the Sprite base image PR** (out-of-repo) in parallel with step 1; aim to land it before step 3 deploys.

## Open questions

1. **Model registry source.** Hardcoded dict (small, pinned) vs. dynamic fetch (opencode does this from models.dev). Hardcoded for v1. Revisit if maintenance burden grows or if customers ask for "use a model that just shipped without waiting for a fairy release."

2. **Cross-runtime command-build seams.** `build_command` returns `list[str]` — does it also need to return env var overrides (e.g. a runtime that wants a non-credential env var set just for this command)? Lean no for v1; if it comes up, runtimes can write those into the session-specific config file instead.

3. **`AgentSession.runtime` denormalization.** Keep for query convenience or drop in favor of joining through `agent_id`? Lean keep — it's cheap and avoids JOIN-on-every-list. Same logic for `AgentVersion`.

4. **Sprite base image PR ownership.** Unchanged from prior docs — who lands the base image PR that pre-installs `goose`, `opencode-ai`, and any future binaries?

5. **Provider env var coverage in `CREDENTIAL_ENV_VAR`.** v1 covers the common providers (anthropic, openai, google). Bedrock notably uses *multiple* env vars (`AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BEARER_TOKEN_BEDROCK`). The `UserCredential.value` is a single string — for multi-var providers, we either store JSON in `value` or model multi-var providers as multiple `UserCredential` rows with kind suffixes (`provider:bedrock:access_key_id`, etc.). Defer until Bedrock support is actually requested.

6. **Skills format unification.** Goose recipes are YAML with structure; SKILL.md is free-form Markdown. Translation is plausible (wrap SKILL.md content as the `instructions:` field of a recipe) but adds runtime-specific serialization to the skills writer. v1: `GooseRuntime.skills_root = None`. Revisit if customers want skills to work with goose.

## Source documents

- [`thoughts/research/2026-04-21-goose-runtime-support.md`](2026-04-21-goose-runtime-support.md) — 4-researcher team-research; original goose integration analysis
- [`thoughts/research/2026-04-21-opencode-runtime-support.md`](2026-04-21-opencode-runtime-support.md) — 5-researcher team-research; original opencode integration analysis

The two source docs document the *symptoms* (env var aliases, model-string prefixes, test invariants that no longer fit). This doc proposes the *cure* (rotate the data model, make Runtime a class, dump all credentials).
