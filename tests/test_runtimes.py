from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.sprites_exec import build_wrapper_script


def test_all_runtimes_defined():
    assert set(RUNTIMES) == {"claude", "codex", "gemini", "claude-oauth"}


def test_wrapper_script_reads_prompt_from_file():
    """PROMPT is per-turn state; the script reads it from a file rather than
    baking it in, so turn 2+ don't need to rewrite the script."""
    config = RUNTIMES["claude"]
    script = build_wrapper_script(config, "sk-test-key")
    assert "sk-test-key" in script
    assert config.env_var in script
    # Prompt is NOT baked into the script; it's loaded from the prompt file.
    assert "/tmp/aod-prompt.txt" in script
    assert "PROMPT=$(cat" in script
    # cmd template still references "$PROMPT"
    assert '"$PROMPT"' in script


def test_wrapper_script_escapes_api_key():
    config = RUNTIMES["claude"]
    script = build_wrapper_script(config, "key with spaces & $pecial")
    # The key should be shell-quoted
    assert "key with spaces" in script
    assert script.count("export ANTHROPIC_API_KEY=") == 1


def test_wrapper_script_dispatches_by_mode():
    """Script takes $1 = run|continue so the same script serves every turn."""
    config = RUNTIMES["claude"]
    script = build_wrapper_script(config, "sk-test")
    assert 'MODE="${1:-run}"' in script
    assert "case \"$MODE\" in" in script
    assert config.cmd in script
    assert config.continue_cmd in script


def test_wrapper_script_exports_session_id_when_provided():
    """runtime_session_id is baked into the script as AOD_SESSION_ID so the
    claude cmd can reference --session-id / --resume by UUID."""
    config = RUNTIMES["claude"]
    sid = "11111111-2222-3333-4444-555555555555"
    script = build_wrapper_script(config, "sk-test", runtime_session_id=sid)
    assert f"export AOD_SESSION_ID={sid}" in script


def test_wrapper_script_no_session_id_when_none():
    """Omitting runtime_session_id leaves AOD_SESSION_ID unset (old sessions,
    or runtimes that don't need it)."""
    config = RUNTIMES["codex"]
    script = build_wrapper_script(config, "sk-test")
    assert "AOD_SESSION_ID" not in script


def test_claude_runtime_uses_session_id_and_resume():
    """Claude's cmd templates use --session-id on run and --resume on continue
    (both referencing $AOD_SESSION_ID)."""
    claude = RUNTIMES["claude"]
    assert '--session-id "$AOD_SESSION_ID"' in claude.cmd
    assert '--resume "$AOD_SESSION_ID"' in claude.continue_cmd
    # Ditto for the OAuth variant, which shares the claude CLI
    oauth = RUNTIMES["claude-oauth"]
    assert '--session-id "$AOD_SESSION_ID"' in oauth.cmd
    assert '--resume "$AOD_SESSION_ID"' in oauth.continue_cmd


def test_each_runtime_has_unique_env_var():
    env_vars = [r.env_var for r in RUNTIMES.values()]
    assert len(env_vars) == len(set(env_vars))


def test_wrapper_script_emits_structured_failure_marker():
    """Any bash command that fails under `set -e` triggers the ERR trap,
    which writes a single AOD_STAGE_FAILED line to stderr identifying the
    failing command. This is the operator's structured handle on "which
    step blew up" — without it, debugging means grepping raw stderr."""
    config = RUNTIMES["claude"]
    script = build_wrapper_script(config, "sk-test")
    assert "trap '__aod_on_err" in script
    assert "AOD_STAGE_FAILED" in script
    # -E is what makes the ERR trap fire inside functions/subshells too.
    assert "set -Eeuo pipefail" in script


def test_wrapper_script_cleans_credentials_on_exit():
    """The EXIT trap unconditionally clears /tmp/.git-credentials so a crash
    mid-clone doesn't leave a GitHub token on the Sprite filesystem."""
    config = RUNTIMES["claude"]
    script = build_wrapper_script(config, "sk-test")
    assert "trap __aod_cleanup EXIT" in script
    assert "rm -f /tmp/.git-credentials" in script
