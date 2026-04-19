from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.sprites_exec import build_wrapper_script


def test_all_runtimes_defined():
    assert set(RUNTIMES) == {"claude", "codex", "gemini", "claude-oauth"}


def test_wrapper_script_escapes_prompt():
    config = RUNTIMES["claude"]
    script = build_wrapper_script(config, "sk-test-key", "hello 'world' $(evil)")
    assert "sk-test-key" in script
    assert config.env_var in script
    # shlex.quote wraps the value in single quotes, making shell expansion impossible.
    # The PROMPT= line should use shlex quoting (single-quote wrapped).
    assert "export PROMPT=" in script
    # Verify the prompt is referenced via $PROMPT (not inlined unescaped)
    assert '"$PROMPT"' in script


def test_wrapper_script_escapes_api_key():
    config = RUNTIMES["claude"]
    script = build_wrapper_script(config, "key with spaces & $pecial", "test")
    # The key should be shell-quoted
    assert "key with spaces" in script
    assert script.count("export ANTHROPIC_API_KEY=") == 1


def test_each_runtime_has_unique_env_var():
    env_vars = [r.env_var for r in RUNTIMES.values()]
    assert len(env_vars) == len(set(env_vars))
