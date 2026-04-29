from __future__ import annotations

from uuid import uuid4

import pytest

import pydantic

from aod import (
    ConflictError,
    GithubRepoResource,
    RateLimitError,
    RunResult,
    Session,
    SessionAck,
    SessionTurn,
)


def test_list(client, server, make_session):
    s = make_session()
    server.json("GET", "/sessions", 200, {"data": [s]})
    sessions = client.sessions.list()
    assert isinstance(sessions[0], Session)


def test_create_returns_ack_shape(client, server):
    ack_id = str(uuid4())
    server.json(
        "POST",
        "/sessions",
        202,
        {
            "id": ack_id,
            "status": "pending",
            "stream_url": f"/sessions/{ack_id}/stream",
            "environment_id": None,
            "resources": [],
            "current_turn": 1,
        },
    )

    ack = client.sessions.create(agent_id=uuid4(), prompt="hi", timeout=30)
    assert isinstance(ack, SessionAck)
    assert ack.current_turn == 1
    assert ack.status == "pending"


def test_create_rate_limited_raises_with_limit_active(client, server):
    server.json(
        "POST",
        "/sessions",
        429,
        {"detail": "limit reached", "limit": 3, "active": 3},
    )
    with pytest.raises(RateLimitError) as excinfo:
        client.sessions.create(agent_id=uuid4(), prompt="hi")
    assert excinfo.value.limit == 3
    assert excinfo.value.active == 3


def test_get(client, server, make_session):
    s = make_session()
    server.json("GET", f"/sessions/{s['id']}", 200, s)
    result = client.sessions.get(s["id"])
    assert result.status == "completed"


def test_prompt_on_running_session_conflicts(client, server):
    sid = str(uuid4())
    server.json(
        "POST",
        f"/sessions/{sid}/prompt",
        409,
        {"detail": "Session is already running"},
    )
    with pytest.raises(ConflictError):
        client.sessions.prompt(sid, prompt="again", timeout=30)


def test_prompt_happy_path(client, server):
    sid = str(uuid4())
    server.json(
        "POST",
        f"/sessions/{sid}/prompt",
        202,
        {
            "id": sid,
            "status": "pending",
            "stream_url": f"/sessions/{sid}/stream",
            "current_turn": 2,
        },
    )
    ack = client.sessions.prompt(sid, prompt="next", timeout=60)
    assert ack.current_turn == 2
    assert server.requests[-1].body == {"prompt": "next", "timeout": 60}


def test_turns(client, server):
    from datetime import datetime, timezone

    sid = str(uuid4())
    turn = {
        "turn_number": 1,
        "prompt": "hi",
        "status": "completed",
        "exit_code": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
    }
    server.json("GET", f"/sessions/{sid}/turns", 200, {"data": [turn]})
    result = client.sessions.turns(sid)
    assert isinstance(result[0], SessionTurn)
    assert result[0].exit_code == 0


def test_terminate(client, server):
    sid = str(uuid4())
    server.json("POST", f"/sessions/{sid}/terminate", 200, {"id": sid, "status": "terminated"})
    ack = client.sessions.terminate(sid)
    assert ack.status == "terminated"


def test_terminate_already_terminated(client, server):
    sid = str(uuid4())
    server.json(
        "POST",
        f"/sessions/{sid}/terminate",
        409,
        {"detail": "Session is already terminated"},
    )
    with pytest.raises(ConflictError):
        client.sessions.terminate(sid)


def test_delete(client, server):
    sid = str(uuid4())
    server.json("DELETE", f"/sessions/{sid}/delete", 200, {"detail": "Session deleted"})
    assert client.sessions.delete(sid) is None


def test_stream_passes_since_param(client, server):
    sid = str(uuid4())

    def responder(request):
        import httpx

        body = b'data: {"type":"start","session_id":"' + sid.encode() + b'"}\n\n'
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    server.register("GET", f"/sessions/{sid}/stream", responder)

    with client.sessions.stream(sid, since=42) as events:
        collected = list(events)

    assert collected[0].type == "start"
    assert server.requests[-1].params.get("since") == ["42"]


def test_create_with_typed_github_resource(client, server):
    ack_id = str(uuid4())
    server.json(
        "POST",
        "/sessions",
        202,
        {"id": ack_id, "status": "pending", "current_turn": 1},
    )

    client.sessions.create(
        agent_id=uuid4(),
        prompt="hi",
        resources=[
            GithubRepoResource(
                url="https://github.com/me/repo",
                authorization_token="ghp_secret",
            ),
        ],
    )

    sent = server.requests[-1].body["resources"]
    assert sent == [
        {
            "type": "github_repository",
            "url": "https://github.com/me/repo",
            "authorization_token": "ghp_secret",
        }
    ]


def test_create_with_typed_github_resource_custom_mount(client, server):
    ack_id = str(uuid4())
    server.json("POST", "/sessions", 202, {"id": ack_id, "status": "pending", "current_turn": 1})

    client.sessions.create(
        agent_id=uuid4(),
        prompt="hi",
        resources=[
            GithubRepoResource(url="https://github.com/me/repo", mount_path="/repo"),
        ],
    )

    assert server.requests[-1].body["resources"][0]["mount_path"] == "/repo"


def test_create_with_dict_resources_still_works(client, server):
    ack_id = str(uuid4())
    server.json("POST", "/sessions", 202, {"id": ack_id, "status": "pending", "current_turn": 1})

    client.sessions.create(
        agent_id=uuid4(),
        prompt="hi",
        resources=[{"type": "github_repository", "url": "https://github.com/me/repo"}],
    )

    assert server.requests[-1].body["resources"] == [
        {"type": "github_repository", "url": "https://github.com/me/repo"}
    ]


def test_create_with_dot_git_suffixed_url(client, server):
    """`Field(pattern=...)` must allow the `.git` suffix end-to-end."""
    ack_id = str(uuid4())
    server.json("POST", "/sessions", 202, {"id": ack_id, "status": "pending", "current_turn": 1})

    client.sessions.create(
        agent_id=uuid4(),
        prompt="hi",
        resources=[GithubRepoResource(url="https://github.com/me/repo.git")],
    )

    assert server.requests[-1].body["resources"][0]["url"] == "https://github.com/me/repo.git"


def test_github_resource_rejects_non_github_url():
    with pytest.raises(pydantic.ValidationError):
        GithubRepoResource(url="https://gitlab.com/me/repo")


def test_github_resource_rejects_relative_mount_path():
    with pytest.raises(pydantic.ValidationError):
        GithubRepoResource(url="https://github.com/me/repo", mount_path="repo")


def test_github_resource_rejects_reserved_mount_path():
    """`/` and `/home/sprite` would shadow the Sprite working directory."""
    with pytest.raises(pydantic.ValidationError):
        GithubRepoResource(url="https://github.com/me/repo", mount_path="/")
    with pytest.raises(pydantic.ValidationError):
        GithubRepoResource(url="https://github.com/me/repo", mount_path="/home/sprite")


def test_github_resource_rejects_reserved_mount_path_trailing_slash():
    """`/home/sprite/` resolves to the same dir as `/home/sprite`; reject both."""
    with pytest.raises(pydantic.ValidationError):
        GithubRepoResource(url="https://github.com/me/repo", mount_path="/home/sprite/")


def _stream_responder(events: list[bytes]):
    import httpx

    def responder(_):
        body = b"".join(b"data: " + e + b"\n\n" for e in events)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return responder


def test_run_creates_streams_and_fetches_final(client, server, make_session):
    sid = str(uuid4())
    final = make_session(id=sid, status="completed", exit_code=0, current_turn=1)
    server.json(
        "POST",
        "/sessions",
        202,
        {"id": sid, "status": "pending", "current_turn": 1},
    )
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder(
            [
                b'{"type":"start","session_id":"' + sid.encode() + b'"}',
                b'{"type":"output","id":1,"stream":"stdout","data":"hi"}',
                b'{"type":"exit","id":2,"exit_code":0}',
            ]
        ),
    )
    server.json("GET", f"/sessions/{sid}", 200, final)

    result = client.sessions.run(agent_id=uuid4(), prompt="hi")

    assert isinstance(result, RunResult)
    assert isinstance(result.session, Session)
    assert result.session.status == "completed"
    assert result.session.exit_code == 0
    assert [e.type for e in result.events] == ["start", "output", "exit"]


def test_run_stops_at_first_terminal_event(client, server, make_session):
    """Events after `exit`/`error`/`terminated` are not consumed."""
    sid = str(uuid4())
    server.json("POST", "/sessions", 202, {"id": sid, "status": "pending"})
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder(
            [
                b'{"type":"start"}',
                b'{"type":"error","id":1,"detail":"boom"}',
                b'{"type":"output","id":2,"data":"never seen"}',
            ]
        ),
    )
    server.json("GET", f"/sessions/{sid}", 200, make_session(id=sid, status="failed"))

    result = client.sessions.run(agent_id=uuid4(), prompt="x")

    assert [e.type for e in result.events] == ["start", "error"]
    assert result.session.status == "failed"


def test_run_calls_on_event(client, server, make_session):
    sid = str(uuid4())
    server.json("POST", "/sessions", 202, {"id": sid, "status": "pending"})
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder(
            [
                b'{"type":"start"}',
                b'{"type":"output","id":1,"data":"hi"}',
                b'{"type":"exit","id":2,"exit_code":0}',
            ]
        ),
    )
    server.json("GET", f"/sessions/{sid}", 200, make_session(id=sid))

    seen = []
    client.sessions.run(agent_id=uuid4(), prompt="x", on_event=lambda e: seen.append(e.type))

    assert seen == ["start", "output", "exit"]


def test_run_with_collect_events_false(client, server, make_session):
    sid = str(uuid4())
    server.json("POST", "/sessions", 202, {"id": sid, "status": "pending"})
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder(
            [b'{"type":"start"}', b'{"type":"exit","id":1,"exit_code":0}']
        ),
    )
    server.json("GET", f"/sessions/{sid}", 200, make_session(id=sid))

    result = client.sessions.run(agent_id=uuid4(), prompt="x", collect_events=False)

    assert result.events == []
    assert result.session.id is not None


@pytest.mark.asyncio
async def test_async_run(async_client, server, make_session):
    sid = str(uuid4())
    server.json("POST", "/sessions", 202, {"id": sid, "status": "pending"})
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder(
            [b'{"type":"start"}', b'{"type":"exit","id":1,"exit_code":0}']
        ),
    )
    server.json("GET", f"/sessions/{sid}", 200, make_session(id=sid, status="completed"))

    async with async_client as c:
        result = await c.sessions.run(agent_id=uuid4(), prompt="hi")

    assert result.session.status == "completed"
    assert [e.type for e in result.events] == ["start", "exit"]


@pytest.mark.asyncio
async def test_async_run_supports_async_on_event(async_client, server, make_session):
    sid = str(uuid4())
    server.json("POST", "/sessions", 202, {"id": sid, "status": "pending"})
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder([b'{"type":"start"}', b'{"type":"exit","id":1,"exit_code":0}']),
    )
    server.json("GET", f"/sessions/{sid}", 200, make_session(id=sid))

    seen = []

    async def on_event(e):
        seen.append(e.type)

    async with async_client as c:
        await c.sessions.run(agent_id=uuid4(), prompt="x", on_event=on_event)

    assert seen == ["start", "exit"]


def test_wait_for_completion_streams_existing_session(client, server, make_session):
    """Caller has a session id from elsewhere — block until terminal."""
    sid = str(uuid4())
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder(
            [
                b'{"type":"start"}',
                b'{"type":"output","id":1,"data":"hi"}',
                b'{"type":"exit","id":2,"exit_code":0}',
            ]
        ),
    )
    server.json(
        "GET", f"/sessions/{sid}", 200, make_session(id=sid, status="completed", exit_code=0)
    )

    result = client.sessions.wait_for_completion(sid)

    assert result.session.status == "completed"
    assert [e.type for e in result.events] == ["start", "output", "exit"]


def test_wait_for_completion_passes_since(client, server, make_session):
    sid = str(uuid4())
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder([b'{"type":"exit","id":11,"exit_code":0}']),
    )
    server.json("GET", f"/sessions/{sid}", 200, make_session(id=sid))

    client.sessions.wait_for_completion(sid, since=10)

    stream_request = next(r for r in server.requests if "/stream" in r.path)
    assert stream_request.params.get("since") == ["10"]


def test_wait_for_completion_calls_on_event(client, server, make_session):
    sid = str(uuid4())
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder([b'{"type":"start"}', b'{"type":"exit","id":1,"exit_code":0}']),
    )
    server.json("GET", f"/sessions/{sid}", 200, make_session(id=sid))

    seen = []
    client.sessions.wait_for_completion(sid, on_event=lambda e: seen.append(e.type))

    assert seen == ["start", "exit"]


async def test_async_wait_for_completion(async_client, server, make_session):
    sid = str(uuid4())
    server.register(
        "GET",
        f"/sessions/{sid}/stream",
        _stream_responder([b'{"type":"start"}', b'{"type":"terminated","id":1}']),
    )
    server.json("GET", f"/sessions/{sid}", 200, make_session(id=sid, status="terminated"))

    async with async_client as c:
        result = await c.sessions.wait_for_completion(sid)

    assert result.session.status == "terminated"
    assert [e.type for e in result.events] == ["start", "terminated"]
