"""Direct unit tests for `build_env_file_body`.

Mutation-tested. Each test pins one mutation-killable property of the
`/tmp/aod-env` body:

  - Precedence: credentials → AOD_SESSION_ID → AOD_MODEL → sorted
    Environment.env_vars. A swap or drop here silently breaks env-var
    overrides on every session.
  - `shlex.quote` is applied to every value — payloads with spaces,
    single quotes, and dollar signs verify quoting is active.
  - Falsy `runtime_session_id` (None or "") and `model` (None or "") are
    omitted entirely.
  - `env_vars` keys are emitted in alphabetical order regardless of
    dict insertion order.
  - The body always ends with exactly one trailing newline (not zero,
    not two).
  - Empty inputs across the board → body is exactly ``"\\n"``.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``SessionSpec`` and ``Environment`` are
duck-typed via ``SimpleNamespace``.
"""

from types import SimpleNamespace

from agent_on_demand.session_service.env_file import build_env_file_body


def _spec(runtime_session_id=None, model="", environment=None):
    return SimpleNamespace(
        runtime_session_id=runtime_session_id,
        model=model,
        environment=environment,
    )


def _env(env_vars=None):
    return SimpleNamespace(env_vars=env_vars or {})


# ---------- empty inputs ----------


def test_empty_inputs_produces_single_newline():
    assert build_env_file_body(_spec(), []) == "\n"


def test_none_environment_produces_no_env_var_lines():
    assert build_env_file_body(_spec(environment=None), []) == "\n"


# ---------- ordering: credentials → AOD_SESSION_ID → AOD_MODEL → env_vars ----------


def test_credential_precedes_session_id():
    spec = _spec(runtime_session_id="sess-1")
    body = build_env_file_body(spec, [("ANTHROPIC_API_KEY", "key123")])
    lines = body.rstrip("\n").splitlines()
    cred_idx = next(i for i, ln in enumerate(lines) if ln.startswith("ANTHROPIC_API_KEY="))
    sid_idx = next(i for i, ln in enumerate(lines) if ln.startswith("AOD_SESSION_ID="))
    assert cred_idx < sid_idx


def test_session_id_precedes_model():
    spec = _spec(runtime_session_id="sess-1", model="claude-3")
    body = build_env_file_body(spec, [])
    lines = body.rstrip("\n").splitlines()
    sid_idx = next(i for i, ln in enumerate(lines) if ln.startswith("AOD_SESSION_ID="))
    model_idx = next(i for i, ln in enumerate(lines) if ln.startswith("AOD_MODEL="))
    assert sid_idx < model_idx


def test_model_precedes_env_vars():
    spec = _spec(model="claude-3", environment=_env({"ZKEY": "z"}))
    body = build_env_file_body(spec, [])
    lines = body.rstrip("\n").splitlines()
    model_idx = next(i for i, ln in enumerate(lines) if ln.startswith("AOD_MODEL="))
    env_idx = next(i for i, ln in enumerate(lines) if ln.startswith("ZKEY="))
    assert model_idx < env_idx


def test_credentials_precede_env_vars():
    spec = _spec(environment=_env({"AKEY": "val"}))
    body = build_env_file_body(spec, [("OPENAI_API_KEY", "sk-xyz")])
    lines = body.rstrip("\n").splitlines()
    cred_idx = next(i for i, ln in enumerate(lines) if ln.startswith("OPENAI_API_KEY="))
    env_idx = next(i for i, ln in enumerate(lines) if ln.startswith("AKEY="))
    assert cred_idx < env_idx


def test_full_body_emits_in_documented_order():
    spec = _spec(
        runtime_session_id="sess-1",
        model="claude-3",
        environment=_env({"ZZZ": "z", "AAA": "a"}),
    )
    body = build_env_file_body(spec, [("CRED1", "v1"), ("CRED2", "v2")])
    keys = [ln.split("=", 1)[0] for ln in body.rstrip("\n").splitlines()]
    assert keys == ["CRED1", "CRED2", "AOD_SESSION_ID", "AOD_MODEL", "AAA", "ZZZ"]


# ---------- shlex.quote applied to all values ----------


def test_quote_applied_to_credential_with_spaces():
    body = build_env_file_body(_spec(), [("MY_KEY", "hello world")])
    assert "MY_KEY='hello world'" in body


def test_quote_applied_to_credential_with_single_quote():
    body = build_env_file_body(_spec(), [("MY_KEY", "it's")])
    # shlex.quote escapes single quotes — bare unquoted form must not appear.
    assert "MY_KEY=it's\n" not in body
    assert "MY_KEY=" in body


def test_quote_applied_to_credential_with_dollar_sign():
    body = build_env_file_body(_spec(), [("MY_KEY", "$SECRET")])
    assert "MY_KEY='$SECRET'" in body


def test_quote_applied_to_session_id_with_spaces():
    body = build_env_file_body(_spec(runtime_session_id="has space"), [])
    assert "AOD_SESSION_ID='has space'" in body


def test_quote_applied_to_model_with_dollar_sign():
    body = build_env_file_body(_spec(model="$MODEL"), [])
    assert "AOD_MODEL='$MODEL'" in body


def test_quote_applied_to_env_var_value_with_spaces():
    body = build_env_file_body(_spec(environment=_env({"FOO": "bar baz"})), [])
    assert "FOO='bar baz'" in body


# ---------- omission of falsy session_id / model ----------


def test_none_session_id_omitted():
    body = build_env_file_body(_spec(runtime_session_id=None, model="claude-3"), [])
    assert "AOD_SESSION_ID" not in body


def test_empty_string_session_id_omitted():
    body = build_env_file_body(_spec(runtime_session_id=""), [])
    assert "AOD_SESSION_ID" not in body


def test_empty_model_omitted():
    body = build_env_file_body(_spec(runtime_session_id="s1", model=""), [])
    assert "AOD_MODEL" not in body


def test_none_model_omitted():
    body = build_env_file_body(_spec(model=None), [])
    assert "AOD_MODEL" not in body


# ---------- env_vars alphabetical ordering ----------


def test_env_vars_emitted_in_alphabetical_order():
    spec = _spec(environment=_env({"BETA": "2", "ALPHA": "1"}))
    body = build_env_file_body(spec, [])
    keys = [ln.split("=", 1)[0] for ln in body.rstrip("\n").splitlines()]
    assert keys == ["ALPHA", "BETA"]


def test_env_vars_three_keys_alphabetical():
    spec = _spec(environment=_env({"ZZZ": "z", "AAA": "a", "MMM": "m"}))
    body = build_env_file_body(spec, [])
    keys = [ln.split("=", 1)[0] for ln in body.rstrip("\n").splitlines()]
    assert keys == ["AAA", "MMM", "ZZZ"]


# ---------- credentials preserve caller-supplied order ----------


def test_multiple_credentials_order_preserved():
    creds = [("FIRST", "a"), ("SECOND", "b"), ("THIRD", "c")]
    body = build_env_file_body(_spec(), creds)
    keys = [ln.split("=", 1)[0] for ln in body.rstrip("\n").splitlines()]
    assert keys == ["FIRST", "SECOND", "THIRD"]


# ---------- trailing-newline invariant ----------


def test_body_ends_with_single_newline_when_empty():
    body = build_env_file_body(_spec(), [])
    assert body.endswith("\n")
    assert not body.endswith("\n\n")


def test_body_ends_with_single_newline_with_content():
    spec = _spec(runtime_session_id="s", model="m", environment=_env({"K": "v"}))
    body = build_env_file_body(spec, [("X", "y")])
    assert body.endswith("\n")
    assert not body.endswith("\n\n")


def test_body_ends_with_single_newline_credential_only():
    body = build_env_file_body(_spec(), [("K", "v")])
    assert body.endswith("\n")
    assert not body.endswith("\n\n")


# ---------- value-side correctness for individual lines ----------


def test_session_id_value_is_quoted_simple():
    body = build_env_file_body(_spec(runtime_session_id="abc"), [])
    assert "AOD_SESSION_ID=abc\n" in body


def test_model_value_is_quoted_simple():
    body = build_env_file_body(_spec(model="claude-3"), [])
    assert "AOD_MODEL=claude-3\n" in body


def test_env_var_value_is_quoted_simple():
    body = build_env_file_body(_spec(environment=_env({"FOO": "bar"})), [])
    assert "FOO=bar\n" in body
