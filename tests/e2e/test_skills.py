"""E2E tests that each runtime actually *uses* skills we materialize.

Materialization-only checks (was the SKILL.md written?) don't prove the
runtime's discovery wired it into the model's context. These tests put a
random verification code inside a skill body and instruct the model to
include it when the skill is invoked — the code appearing in session
output proves the runtime read the body, loaded it, and the model
followed it.
"""

import json
import uuid

import pytest

from tests.e2e.conftest import RUNTIME_MODELS, _unique, stream_all_output

pytestmark = pytest.mark.slow


# Fields whose string values are event metadata, not model text. Filtering
# them out keeps metadata from wedging itself between chunked content deltas
# and breaking substring matches.
_METADATA_KEYS = frozenset({
    "type", "role", "session_id", "model", "timestamp", "id", "tool_id",
    "tool_name", "tool_call_id", "tool_use_id", "parent_tool_use_id",
    "status", "finish_reason", "stop_reason", "event_type", "msg_type",
    "name", "uuid",
})


def _concat_json_strings(stream_output: str) -> str:
    """Glue chunked deltas back together across runtime output formats.

    All three runtimes (claude stream-json, codex --json, gemini stream-json)
    emit one JSON object per line, with model text split across many
    ``content`` / ``text`` delta events. A substring match on the raw stream
    fails when the target string straddles two events — the JSON boundary
    (``",...","content":"``) gets wedged into the middle. Walking every
    parsed event and concatenating string values (minus known metadata
    fields) reassembles the text regardless of shape.
    """
    parts: list[str] = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k in _METADATA_KEYS:
                    continue
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for line in stream_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            parts.append(line)
    return "".join(parts)


@pytest.fixture(params=list(RUNTIME_MODELS.keys()))
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    if request.param == "codex":
        # Codex differs from claude/gemini: it injects only the skill
        # metadata (name + description), then expects the model to
        # `cat SKILL.md` on demand. Inside the Sprite sandbox, bwrap
        # currently rejects that cat with:
        #   bwrap: Unexpected capabilities but not setuid, old file caps
        # Materialization + metadata injection verifiably work; only the
        # body-read step is broken, and the root cause is in the Sprite
        # image (bwrap file caps / setuid), not in Fairy. Flip to strict
        # so we get a loud signal when the sandbox is fixed upstream.
        request.applymarker(
            pytest.mark.xfail(
                reason=(
                    "Sprite bwrap caps config blocks codex from cat'ing "
                    "SKILL.md; skill body never reaches the model."
                ),
                strict=True,
            )
        )
    return request.param


def test_skill_is_invoked_by_runtime(api, create_agent, create_session, runtime):
    """Agent with an `info` skill about goats + a verification code.

    Prompt asks for goat info; output must contain both a goat reference
    and the unique verification code, proving the skill body reached the
    model.
    """
    code = f"GOAT-CODE-{uuid.uuid4().hex[:12]}"
    skill_name = "info"
    skill_description = (
        "Canonical project info. Invoke this skill whenever the user asks "
        "for info or asks about goats."
    )
    skill_content = (
        f"---\n"
        f"name: {skill_name}\n"
        f"description: {skill_description}\n"
        f"---\n"
        f"\n"
        f"When this skill is invoked, your response MUST include both of the "
        f"following, verbatim:\n"
        f"\n"
        f"1. A sentence about goats (the animal). Mention the word \"goat\" "
        f"explicitly.\n"
        f"2. The verification code exactly as written: {code}\n"
        f"\n"
        f"Do not paraphrase the code. Do not omit either item.\n"
    )

    agent = create_agent(
        name=_unique(f"e2e-skill-use-{runtime}"),
        model=RUNTIME_MODELS[runtime],
        runtime=runtime,
        skills=[
            {
                "name": skill_name,
                "description": skill_description,
                "content": skill_content,
            }
        ],
    )

    session = create_session(
        agent_id=agent["id"],
        prompt=(
            f"Use the `{skill_name}` skill and tell me about goats. "
            f"Follow the skill's instructions exactly."
        ),
        timeout=180,
    )

    result, events = api.run_session(session["id"])
    assert result["status"] == "completed", (
        f"Session failed: status={result['status']}, "
        f"exit_code={result.get('exit_code')}"
    )

    raw = stream_all_output(events)
    reassembled = _concat_json_strings(raw)
    assert code in reassembled, (
        f"Verification code {code!r} missing — skill body didn't reach the model. "
        f"Raw output (first 800): {raw[:800]!r}"
    )
    assert "goat" in reassembled.lower(), (
        f"No mention of goats in output — skill instructions not followed. "
        f"Raw output (first 800): {raw[:800]!r}"
    )
