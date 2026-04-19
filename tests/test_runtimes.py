from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service.dispatcher import mcp_cmd_flags, render_dispatcher_script


def test_all_runtimes_defined():
    assert set(RUNTIMES) == {"claude", "codex", "gemini", "claude-oauth"}


def test_dispatcher_reads_prompt_from_file():
    """PROMPT is per-turn state; the dispatcher reads it from a file rather
    than baking it in, so turn 2+ don't need to rewrite the script."""
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config, has_mcp=False)
    assert "/tmp/aod-prompt.txt" in script
    assert "PROMPT=$(cat" in script
    assert '"$PROMPT"' in script


def test_dispatcher_sources_env_file():
    """The dispatcher sources /tmp/aod-env (with `set -a`) so the runtime API
    key and AOD_SESSION_ID are exported at exec time without being baked into
    the script body."""
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config, has_mcp=False)
    assert "source /tmp/aod-env" in script
    assert "set -a" in script
    # API key env-var name never appears in the dispatcher — it only lives
    # in the env file.
    assert config.env_var not in script


def test_dispatcher_has_no_setup_sections():
    """All setup (packages, clone, MCP, skills) ran during provision_session.
    The dispatcher does not re-do any of it, and it carries no sentinel."""
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config, has_mcp=True)
    assert "apt-get" not in script
    assert "pip install" not in script
    assert "git clone" not in script
    assert "mkdir -p /home/sprite/.claude/skills" not in script
    assert "SKILL_EOF" not in script
    assert "MCP_EOF" not in script
    assert "/tmp/aod-initialized" not in script


def test_dispatcher_dispatches_by_mode():
    """Dispatcher takes $1 = run|continue so the same script serves every turn."""
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config, has_mcp=False)
    assert 'MODE="${1:-run}"' in script
    assert 'case "$MODE" in' in script
    assert config.cmd in script
    assert config.continue_cmd in script


def test_claude_runtime_uses_session_id_and_resume():
    claude = RUNTIMES["claude"]
    assert '--session-id "$AOD_SESSION_ID"' in claude.cmd
    assert '--resume "$AOD_SESSION_ID"' in claude.continue_cmd
    oauth = RUNTIMES["claude-oauth"]
    assert '--session-id "$AOD_SESSION_ID"' in oauth.cmd
    assert '--resume "$AOD_SESSION_ID"' in oauth.continue_cmd


def test_each_runtime_has_unique_env_var():
    env_vars = [r.env_var for r in RUNTIMES.values()]
    assert len(env_vars) == len(set(env_vars))


def test_dispatcher_emits_structured_failure_marker():
    """The ERR trap writes a single AOD_STAGE_FAILED line to stderr when any
    command in the dispatcher fails under `set -e`. This is what operators
    grep for when the runtime CLI blows up mid-turn."""
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config, has_mcp=False)
    assert "trap '__aod_on_err" in script
    assert "AOD_STAGE_FAILED" in script
    assert "set -Eeuo pipefail" in script


def test_mcp_cmd_flags_only_for_claude():
    assert mcp_cmd_flags("claude", has_mcp=True).startswith(" --mcp-config")
    assert mcp_cmd_flags("claude-oauth", has_mcp=True).startswith(" --mcp-config")
    assert mcp_cmd_flags("codex", has_mcp=True) == ""
    assert mcp_cmd_flags("gemini", has_mcp=True) == ""
    assert mcp_cmd_flags("claude", has_mcp=False) == ""


def test_mcp_flags_appear_in_dispatcher_when_has_mcp():
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config, has_mcp=True)
    assert "--mcp-config /tmp/mcp.json" in script
    assert "--strict-mcp-config" in script


def test_no_mcp_flags_when_has_mcp_false():
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config, has_mcp=False)
    assert "--mcp-config" not in script
