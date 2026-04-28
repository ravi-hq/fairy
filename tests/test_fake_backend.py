"""Sanity tests for the FakeBackend recording fake.

The fake doesn't replace any production behavior, but it underpins every
session_service unit test going forward, so its accounting needs to be
exact: writes, chmods, commands, and network policies should all show up
in the lists tests inspect."""

from __future__ import annotations

import io

import pytest

from agent_on_demand.session_service.backend import (
    BackendError,
    NetworkPolicy,
    PolicyRule,
    SessionNotFoundError,
)
from tests.fakes.backend import FakeBackend, RecordingBackendClient


def test_fake_backend_create_client_returns_recording_client():
    fb = FakeBackend()
    client = fb.create_client("token")
    assert isinstance(client, RecordingBackendClient)


def test_fake_backend_returns_preset_client():
    preset = RecordingBackendClient()
    fb = FakeBackend(client=preset)
    assert fb.create_client("token") is preset


def test_provision_records_name_and_returns_handle():
    client = RecordingBackendClient()
    handle = client.provision("session-1")
    assert client.created == ["session-1"]
    assert handle.name == "session-1"
    assert client.last_handle() is handle


def test_provision_can_be_made_to_raise():
    client = RecordingBackendClient()
    client.raise_on_provision(BackendError("boom"))
    with pytest.raises(BackendError):
        client.provision("name")
    # Single-shot — second call succeeds.
    client.provision("name")


def test_get_returns_existing_handle_after_provision():
    client = RecordingBackendClient()
    handle = client.provision("s")
    assert client.get("s") is handle


def test_get_can_be_marked_missing():
    client = RecordingBackendClient()
    client.mark_missing("ghost")
    with pytest.raises(SessionNotFoundError):
        client.get("ghost")


def test_destroy_records_name():
    client = RecordingBackendClient()
    client.destroy("s")
    assert client.deleted == ["s"]


def test_close_marks_closed():
    client = RecordingBackendClient()
    client.close()
    assert client.closed


# ---------- Handle / Workspace ----------


def test_handle_workspace_records_writes_with_normalized_paths():
    client = RecordingBackendClient()
    handle = client.provision("s")
    fs = handle.workspace()
    fs.write_text("tmp/aod-env", "FOO=bar\n")
    fs.write_text("/tmp/run.sh", "#!/bin/bash\necho hi\n")
    assert [w.path for w in handle.writes] == ["/tmp/aod-env", "/tmp/run.sh"]
    assert handle.writes[0].content == "FOO=bar\n"


def test_handle_workspace_records_chmods():
    client = RecordingBackendClient()
    handle = client.provision("s")
    handle.workspace().chmod("/tmp/run.sh", 0o755)
    assert handle.chmods[0].path == "/tmp/run.sh"
    assert handle.chmods[0].mode == 0o755


def test_handle_raise_on_write_fires_once():
    client = RecordingBackendClient()
    handle = client.provision("s")
    handle.raise_on_write("aod-env", BackendError("disk full"))
    fs = handle.workspace()
    with pytest.raises(BackendError):
        fs.write_text("/tmp/aod-env", "x")
    # Predicate is single-shot — the next write to the same path succeeds.
    fs.write_text("/tmp/aod-env", "y")
    assert handle.writes == [fs.writes[0]]
    assert handle.writes[0].content == "y"


# ---------- Commands ----------


def test_make_command_records_argv_cwd_timeout():
    client = RecordingBackendClient()
    handle = client.provision("s")
    cmd = handle.make_command("bash", "-lc", "true", cwd="/home/sprite", timeout=30.0)
    cmd.run()
    rec = handle.commands[0]
    assert rec.argv == ("bash", "-lc", "true")
    assert rec.cwd == "/home/sprite"
    assert rec.timeout == 30.0
    assert rec.ran is True
    assert rec.exit_code == 0


def test_make_command_set_input_records_stdin():
    client = RecordingBackendClient()
    handle = client.provision("s")
    cmd = handle.make_command("cat")
    cmd.set_input(b"hello")
    cmd.run()
    assert handle.commands[0].stdin == b"hello"


def test_command_outcome_can_be_overridden_with_exit_code():
    client = RecordingBackendClient()
    handle = client.provision("s")
    handle.set_command_outcome(lambda argv: argv[0] == "false", exit_code=1)
    cmd = handle.make_command("false")
    assert cmd.run() == 1


def test_command_outcome_can_write_stderr_to_assigned_buffer():
    client = RecordingBackendClient()
    handle = client.provision("s")
    handle.set_command_outcome("bash", exit_code=2, stderr=b"oh no\n")
    cmd = handle.make_command("bash", "-lc", "exit 2")
    err_buf = io.BytesIO()
    cmd.set_output(io.BytesIO(), err_buf)
    assert cmd.run() == 2
    assert err_buf.getvalue() == b"oh no\n"


def test_command_outcome_can_raise():
    client = RecordingBackendClient()
    handle = client.provision("s")
    handle.set_command_outcome("bash", exc=BackendError("transport"))
    cmd = handle.make_command("bash", "-lc", "true")
    with pytest.raises(BackendError):
        cmd.run()


# ---------- Network policies ----------


def test_apply_network_policy_records_policies():
    client = RecordingBackendClient()
    handle = client.provision("s")
    p = NetworkPolicy(rules=(PolicyRule(domain="x", action="allow"),))
    handle.apply_network_policy(p)
    assert handle.network_policies == [p]


def test_apply_network_policy_can_be_made_to_raise_once():
    client = RecordingBackendClient()
    handle = client.provision("s")
    handle.raise_on_apply_network_policy(BackendError("policy backend down"))
    with pytest.raises(BackendError):
        handle.apply_network_policy(NetworkPolicy())
    # Second call succeeds.
    handle.apply_network_policy(NetworkPolicy(rules=[]))
    assert len(handle.network_policies) == 1


@pytest.mark.django_db
def test_destroy_session_round_trip_through_fake_backend(fake_backend):
    """End-to-end round-trip via the new `fake_backend` fixture: a
    `BackendClient` Protocol-only fake plugged in for `get_client`
    drives `destroy_session` and the deletion is recorded.

    `provision_session` itself is not exercised here because the
    runtime-touching stages still expect the legacy sprite SDK shape
    (PR 3 will port them); this round-trip pins the parts of the
    Protocol that PR 2 already drives end-to-end."""
    from django.contrib.auth.models import User

    from agent_on_demand.models import UserSpritesKey
    from agent_on_demand.session_service.provisioning import destroy_session

    user = User.objects.create_user(username="bk-rt", password="p")
    usk = UserSpritesKey(user=user)
    usk.set_api_key("token")
    usk.save()

    destroy_session(user, "session-xyz")
    assert fake_backend.deleted == ["session-xyz"]
