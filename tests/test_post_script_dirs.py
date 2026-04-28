"""Direct unit tests for `directories_for_post_script_writes`.

Mutation-tested. Each test isolates one mutation-killable branch:

  - Empty input (no MCP servers, no inline skills) → empty list
  - MCP server + per-runtime dir mapping (codex / gemini / opencode)
  - Claude is *intentionally absent* — its config file lives at
    /home/sprite/.claude.json so the parent already exists
  - Unknown runtime is skipped (no crash)
  - Inline skills land under ``<skills_root>/<name>``; github skills
    don't (no ``content`` set)
  - skills_root=None → no inline-skill dirs even if skills are inline

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner) can execute them. ``SessionSpec`` is
duck-typed via ``SimpleNamespace``.
"""

from types import SimpleNamespace

from agent_on_demand.session_service.provisioning.post_script_dirs import (
    directories_for_post_script_writes,
)


def _runtime(name="claude", skills_root=None):
    return SimpleNamespace(name=name, skills_root=skills_root)


def _mcp_server(name="srv"):
    return SimpleNamespace(name=name, type="url", url="https://x", headers={})


def _inline_skill(name, content="body"):
    return SimpleNamespace(name=name, content=content, source=None)


def _github_skill(name="some-skill"):
    return SimpleNamespace(name=name, content=None, source="owner/repo")


def _spec(runtime=None, mcp_servers=None, skills=None):
    return SimpleNamespace(
        runtime=runtime if runtime is not None else _runtime(),
        mcp_servers=mcp_servers if mcp_servers is not None else [],
        skills=skills if skills is not None else [],
    )


# ---------- empty / no-op cases ----------


def test_empty_spec_returns_empty_list():
    """No MCP servers, no skills, no skills_root — nothing to mkdir."""
    assert directories_for_post_script_writes(_spec()) == []


def test_mcp_servers_with_no_runtime_dir_mapping_yields_no_mcp_dir():
    """Claude's MCP config goes to ``/home/sprite/.claude.json`` — the
    parent already exists on the Sprite base image, so claude is
    excluded from the mapping. Pin the exclusion."""
    spec = _spec(runtime=_runtime(name="claude"), mcp_servers=[_mcp_server()])
    assert directories_for_post_script_writes(spec) == []


def test_unknown_runtime_with_mcp_servers_yields_no_mcp_dir():
    """An unknown runtime name silently produces no mkdir entry — same
    as the API validator behavior. Pin so a refactor that adds a raise
    here would have to be a deliberate change."""
    spec = _spec(runtime=_runtime(name="future-runtime"), mcp_servers=[_mcp_server()])
    assert directories_for_post_script_writes(spec) == []


# ---------- per-runtime MCP dir mapping ----------


def test_codex_mcp_dir_is_dotcodex():
    spec = _spec(runtime=_runtime(name="codex"), mcp_servers=[_mcp_server()])
    assert directories_for_post_script_writes(spec) == ["/home/sprite/.codex"]


def test_gemini_mcp_dir_is_dotgemini():
    spec = _spec(runtime=_runtime(name="gemini"), mcp_servers=[_mcp_server()])
    assert directories_for_post_script_writes(spec) == ["/home/sprite/.gemini"]


def test_opencode_mcp_dir_is_dotconfig_opencode():
    """Opencode's config goes under ``.config/opencode/`` (XDG-style),
    not ``.opencode/``. Pinned because it's the only runtime with
    a multi-level path — easy to "simplify" wrong."""
    spec = _spec(runtime=_runtime(name="opencode"), mcp_servers=[_mcp_server()])
    assert directories_for_post_script_writes(spec) == ["/home/sprite/.config/opencode"]


def test_no_mcp_servers_no_mcp_dir_emitted():
    """Even for codex/gemini/opencode, an empty mcp_servers list means
    no mkdir — the post-script doesn't write a config file when there
    are no servers to configure."""
    spec = _spec(runtime=_runtime(name="codex"), mcp_servers=[])
    assert directories_for_post_script_writes(spec) == []


# ---------- inline-skill directories ----------


def test_inline_skill_lands_under_skills_root():
    """Each inline skill needs its directory pre-created — the
    post-script writes the SKILL.md inside it."""
    runtime = _runtime(name="claude", skills_root="/home/sprite/.claude/skills")
    spec = _spec(runtime=runtime, skills=[_inline_skill("hello")])
    assert directories_for_post_script_writes(spec) == ["/home/sprite/.claude/skills/hello"]


def test_multiple_inline_skills_each_get_their_own_dir():
    """Iteration order matches the order the skills appear on the spec."""
    runtime = _runtime(name="claude", skills_root="/skills_root")
    spec = _spec(
        runtime=runtime,
        skills=[_inline_skill("alpha"), _inline_skill("beta"), _inline_skill("gamma")],
    )
    assert directories_for_post_script_writes(spec) == [
        "/skills_root/alpha",
        "/skills_root/beta",
        "/skills_root/gamma",
    ]


def test_github_skill_does_not_get_a_directory():
    """Github-source skills install via ``npx skills add``, which makes
    its own directories. No pre-creation needed — distinguishes a
    mutant that drops the ``s.content is not None`` check."""
    runtime = _runtime(name="claude", skills_root="/skills_root")
    spec = _spec(runtime=runtime, skills=[_github_skill("some-skill")])
    assert directories_for_post_script_writes(spec) == []


def test_inline_and_github_skills_mixed_only_inline_get_dirs():
    """Mix of inline + github skills: only the inline ones get
    mkdir entries."""
    runtime = _runtime(name="claude", skills_root="/skills_root")
    spec = _spec(
        runtime=runtime,
        skills=[_inline_skill("inline"), _github_skill("gh"), _inline_skill("inline2")],
    )
    assert directories_for_post_script_writes(spec) == [
        "/skills_root/inline",
        "/skills_root/inline2",
    ]


def test_no_skills_root_means_no_inline_dirs():
    """If the runtime has no skills_root (e.g. a runtime that doesn't
    support skills), no per-skill dir is emitted — even if inline
    skills are present. Distinguishes a mutant that drops the
    ``if spec.runtime.skills_root`` guard."""
    runtime = _runtime(name="claude", skills_root=None)
    spec = _spec(runtime=runtime, skills=[_inline_skill("hello")])
    assert directories_for_post_script_writes(spec) == []


# ---------- combined (MCP + skills) ----------


def test_mcp_and_inline_skills_both_emit_dirs_in_order():
    """The MCP config dir comes first, then the per-skill dirs in spec
    order. Pinned so a refactor that re-orders these doesn't silently
    break the post-script's mkdir line."""
    runtime = _runtime(name="codex", skills_root="/skills_root")
    spec = _spec(
        runtime=runtime,
        mcp_servers=[_mcp_server()],
        skills=[_inline_skill("foo")],
    )
    assert directories_for_post_script_writes(spec) == [
        "/home/sprite/.codex",
        "/skills_root/foo",
    ]
