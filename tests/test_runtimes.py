from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.stream import _build_turn_command


def test_all_runtimes_defined():
    assert set(RUNTIMES) == {"claude", "codex", "gemini", "claude-oauth"}


def test_turn_command_reads_prompt_from_stdin():
    """PROMPT is per-turn state; the turn command slurps it from stdin so it
    never touches the Sprite filesystem and never leaks into WS URL query
    params (which is what argv / env= would do)."""
    cmd = _build_turn_command(RUNTIMES["claude"], "run")
    assert "PROMPT=$(cat)" in cmd
    assert "/tmp/aod-prompt.txt" not in cmd
    assert '"$PROMPT"' in cmd


def test_turn_command_sources_env_file():
    """The turn command sources /tmp/aod-env (with `set -a`) so the runtime
    API key and AOD_SESSION_ID are exported at exec time without being
    baked into argv (where they would land in WS URL query params)."""
    claude = RUNTIMES["claude"]
    cmd = _build_turn_command(claude, "run")
    assert "source /tmp/aod-env" in cmd
    assert "set -a" in cmd
    # API key env-var name never appears in the command string — it only
    # lives in the env file that bash sources at runtime.
    assert claude.env_var not in cmd


def test_turn_command_selects_by_mode():
    """`run` and `continue` render different runtime CLIs."""
    claude = RUNTIMES["claude"]
    run_cmd = _build_turn_command(claude, "run")
    cont_cmd = _build_turn_command(claude, "continue")
    assert claude.cmd in run_cmd
    assert claude.continue_cmd in cont_cmd
    assert claude.continue_cmd not in run_cmd
    assert claude.cmd not in cont_cmd


def test_turn_command_has_no_setup_sections():
    """All provisioning (packages, clone, MCP, skills) ran in
    `provision_session`. The per-turn command never re-does any of it."""
    cmd = _build_turn_command(RUNTIMES["claude"], "run")
    assert "apt-get" not in cmd
    assert "pip install" not in cmd
    assert "git clone" not in cmd
    assert "mkdir -p /home/sprite/.claude/skills" not in cmd
    assert "/tmp/aod-initialized" not in cmd


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


def test_turn_command_carries_no_mcp_flags():
    """MCP config is auto-discovered from each runtime's default path
    (Claude → ~/.claude.json, Codex → ~/.codex/config.toml, Gemini →
    ~/.gemini/settings.json), so the command needs no --mcp-config flags."""
    for config in RUNTIMES.values():
        for mode in ("run", "continue"):
            cmd = _build_turn_command(config, mode)
            assert "--mcp-config" not in cmd
            assert "--strict-mcp-config" not in cmd
