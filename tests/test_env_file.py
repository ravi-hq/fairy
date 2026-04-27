"""Unit tests for `build_env_file_body`.

Mutation-tested. Each test isolates one mutation-killable branch:

  - Empty inputs (no credentials, no runtime_session_id, no model, no
    env_vars) → body is exactly ``"\\n"``.
  - Credential lines precede AOD_SESSION_ID, which precedes AOD_MODEL,
    which precedes env_vars keys.
  - shlex.quote is applied to every value — payloads with spaces, single
    quotes, and dollar signs verify quoting is active.
  - runtime_session_id=None and model="" both omit their lines.
  - env_vars with out-of-order keys are emitted in alphabetical order.
  - Body always ends with exactly one trailing newline.

Tests are sync, no Django fixtures — required so hammett (mutmut's
runner) can execute them. SessionSpec is duck-typed via SimpleNamespace.
"""

from types import SimpleNamespace

from agent_on_demand.session_service.env_file import build_env_file_body


def _spec(
    runtime_session_id=None,
    model="",
    environment=None,
):
    return SimpleNamespace(
        runtime_session_id=runtime_session_id,
        model=model,
        environment=environment,
    )


def _env(env_vars=None):
    return SimpleNamespace(env_vars=env_vars or {})


# ---------------------------------------------------------------------------
# Empty inputs
# ---------------------------------------------------------------------------


def test_empty_inputs_produces_single_newline():
    spec = _spec()
    body = build_env_file_body(spec, [])
    assert body == "\n"


# ---------------------------------------------------------------------------
# Ordering: credentials → AOD_SESSION_ID → AOD_MODEL → env_vars
# ---------------------------------------------------------------------------


def test_credential_precedes_session_id():
    spec = _spec(runtime_session_id="sess-1")
    body = build_env_file_body(spec, [("ANTHROPIC_API_KEY", "key123")])
    lines = body.rstrip("\n").splitlines()
    cred_idx = next(i for i, l in enumerate(lines) if l.startswith("ANTHROPIC_API_KEY="))
    sid_idx = next(i for i, l in enumerate(lines) if l.startswith("AOD_SESSION_ID="))
    assert cred_idx < sid_idx


def test_session_id_precedes_model():
    spec = _spec(runtime_session_id="sess-1", model="claude-3")
    body = build_env_file_body(spec, [])
    lines = body.rstrip("\n").splitlines()
    sid_idx = next(i for i, l in enumerate(lines) if l.startswith("AOD_SESSION_ID="))
    model_idx = next(i for i, l in enumerate(lines) if l.startswith("AOD_MODEL="))
    assert sid_idx < model_idx


def test_model_precedes_env_vars():
    spec = _spec(model="claude-3", environment=_env({"ZKEY": "z"}))
    body = build_env_file_body(spec, [])
    lines = body.rstrip("\n").splitlines()
    model_idx = next(i for i, l in enumerate(lines) if l.startswith("AOD_MODEL="))
    env_idx = next(i for i, l in enumerate(lines) if l.startswith("ZKEY="))
    assert model_idx < env_idx


def test_credentials_precede_env_vars():
    spec = _spec(environment=_env({"AKEY": "val"}))
    body = build_env_file_body(spec, [("OPENAI_API_KEY", "sk-xyz")])
    lines = body.rstrip("\n").splitlines()
    cred_idx = next(i for i, l in enumerate(lines) if l.startswith("OPENAI_API_KEY="))
    env_idx = next(i for i, l in enumerate(lines) if l.startswith("AKEY="))
    assert cred_idx < env_idx


# ---------------------------------------------------------------------------
# shlex.quote is applied to all values
# ---------------------------------------------------------------------------


def test_quote_applied_to_credential_with_spaces():
    spec = _spec()
    body = build_env_file_body(spec, [("MY_KEY", "hello world")])
    assert "MY_KEY='hello world'" in body


def test_quote_applied_to_credential_with_single_quote():
    spec = _spec()
    body = build_env_file_body(spec, [("MY_KEY", "it's")])
    # shlex.quote escapes single quotes — result is not a bare single-quoted string
    assert "MY_KEY=" in body
    assert "it's" not in body  # raw unquoted form must not appear


def test_quote_applied_to_credential_with_dollar_sign():
    spec = _spec()
    body = build_env_file_body(spec, [("MY_KEY", "$SECRET")])
    assert "MY_KEY='$SECRET'" in body


def test_quote_applied_to_session_id_with_spaces():
    spec = _spec(runtime_session_id="has space")
    body = build_env_file_body(spec, [])
    assert "AOD_SESSION_ID='has space'" in body


def test_quote_applied_to_model_with_dollar_sign():
    spec = _spec(model="$MODEL")
    body = build_env_file_body(spec, [])
    assert "AOD_MODEL='$MODEL'" in body


def test_quote_applied_to_env_var_value_with_spaces():
    spec = _spec(environment=_env({"FOO": "bar baz"}))
    body = build_env_file_body(spec, [])
    assert "FOO='bar baz'" in body


# ---------------------------------------------------------------------------
# Omission of falsy runtime_session_id and model
# ---------------------------------------------------------------------------


def test_none_session_id_omitted():
    spec = _spec(runtime_session_id=None, model="claude-3")
    body = build_env_file_body(spec, [])
    assert "AOD_SESSION_ID" not in body


def test_empty_model_omitted():
    spec = _spec(runtime_session_id="s1", model="")
    body = build_env_file_body(spec, [])
    assert "AOD_MODEL" not in body


def test_empty_string_session_id_omitted():
    spec = _spec(runtime_session_id="")
    body = build_env_file_body(spec, [])
    assert "AOD_SESSION_ID" not in body


def test_none_model_omitted():
    spec = _spec(model=None)
    body = build_env_file_body(spec, [])
    assert "AOD_MODEL" not in body


# ---------------------------------------------------------------------------
# env_vars alphabetical ordering
# ---------------------------------------------------------------------------


def test_env_vars_emitted_in_alphabetical_order():
    spec = _spec(environment=_env({"BETA": "2", "ALPHA": "1"}))
    body = build_env_file_body(spec, [])
    lines = body.rstrip("\n").splitlines()
    keys = [l.split("=", 1)[0] for l in lines]
    assert keys == ["ALPHA", "BETA"]


def test_env_vars_three_keys_alphabetical():
    spec = _spec(environment=_env({"ZZZ": "z", "AAA": "a", "MMM": "m"}))
    body = build_env_file_body(spec, [])
    lines = body.rstrip("\n").splitlines()
    keys = [l.split("=", 1)[0] for l in lines]
    assert keys == ["AAA", "MMM", "ZZZ"]


# ---------------------------------------------------------------------------
# Trailing newline invariant
# ---------------------------------------------------------------------------


def test_body_ends_with_single_newline_empty():
    body = build_env_file_body(_spec(), [])
    assert body.endswith("\n")
    assert not body.endswith("\n\n")


def test_body_ends_with_single_newline_with_lines():
    spec = _spec(runtime_session_id="s", model="m", environment=_env({"K": "v"}))
    body = build_env_file_body(spec, [("X", "y")])
    assert body.endswith("\n")
    assert not body.endswith("\n\n")


def test_body_ends_with_single_newline_credential_only():
    body = build_env_file_body(_spec(), [("K", "v")])
    assert body.endswith("\n")
    assert not body.endswith("\n\n")


# ---------------------------------------------------------------------------
# None environment is treated as no env_vars
# ---------------------------------------------------------------------------


def test_none_environment_produces_no_env_var_lines():
    spec = _spec(environment=None)
    body = build_env_file_body(spec, [])
    assert body == "\n"


# ---------------------------------------------------------------------------
# Multiple credentials in declared order
# ---------------------------------------------------------------------------


def test_multiple_credentials_order_preserved():
    spec = _spec()
    creds = [("FIRST", "a"), ("SECOND", "b"), ("THIRD", "c")]
    body = build_env_file_body(spec, creds)
    lines = body.rstrip("\n").splitlines()
    assert lines[0].startswith("FIRST=")
    assert lines[1].startswith("SECOND=")
    assert lines[2].startswith("THIRD=")
