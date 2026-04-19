from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service.dispatcher import render_dispatcher_script


def test_all_runtimes_defined():
    assert set(RUNTIMES) == {"claude", "codex", "gemini", "claude-oauth"}


def test_dispatcher_reads_prompt_from_stdin():
    """PROMPT is per-turn state; the dispatcher slurps it from stdin so it
    never touches the Sprite filesystem and never leaks into WS URL query
    params (which is what env= / argv would do)."""
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config)
    assert "PROMPT=$(cat)" in script
    assert "/tmp/aod-prompt.txt" not in script
    assert '"$PROMPT"' in script


def test_dispatcher_sources_env_file():
    """The dispatcher sources /tmp/aod-env (with `set -a`) so the runtime API
    key and AOD_SESSION_ID are exported at exec time without being baked into
    the script body."""
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config)
    assert "source /tmp/aod-env" in script
    assert "set -a" in script
    # API key env-var name never appears in the dispatcher — it only lives
    # in the env file.
    assert config.env_var not in script


def test_dispatcher_has_no_setup_sections():
    """All setup (packages, clone, MCP, skills) ran during provision_session.
    The dispatcher does not re-do any of it, and it carries no sentinel."""
    config = RUNTIMES["claude"]
    script = render_dispatcher_script(config)
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
    script = render_dispatcher_script(config)
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
    script = render_dispatcher_script(config)
    assert "trap '__aod_on_err" in script
    assert "AOD_STAGE_FAILED" in script
    assert "set -Eeuo pipefail" in script


def test_dispatcher_carries_no_mcp_flags():
    """MCP config is auto-discovered from each runtime's default path
    (Claude → ~/.claude.json, Codex → ~/.codex/config.toml, Gemini →
    ~/.gemini/settings.json), so the dispatcher needs no --mcp-config flags."""
    for config in RUNTIMES.values():
        script = render_dispatcher_script(config)
        assert "--mcp-config" not in script
        assert "--strict-mcp-config" not in script
