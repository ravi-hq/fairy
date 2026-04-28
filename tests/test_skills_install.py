"""Direct unit tests for `build_skills_install_command`.

Mutation-tested. Each test pins one mutation-killable property of the
shell command handed to ``bash -lc`` for ``skills.sh`` install:

  - The fixed flag set (``npx -y skills@latest add``,
    ``--global``, ``--agent``, ``--yes``) appears in exact order. A
    reorder that places ``--agent`` before ``add`` flips the CLI's
    argument parser into a different mode.
  - User-controlled inputs (``source``, ``agent_id``, ``name``) are
    routed through ``shlex.quote`` before interpolation. A mutant
    that drops the quoting opens a shell-injection sink: ``source``
    is an API-supplied ``owner/repo`` string interpolated into a
    string handed to ``bash -lc``.
  - Falsy ``name`` (``None`` or ``""``) → ``--skill`` flag is
    omitted; truthy ``name`` → ``--skill <quoted-name>`` is appended.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them.
"""

from agent_on_demand.session_service.provisioning.skills_install import build_skills_install_command


# ---------- exact full-string equality (anchor tests) ----------


def test_exact_command_without_name():
    """Full-string equality with shell-safe inputs (``owner/repo`` and
    ``claude`` need no quoting). Anchors the entire command shape so
    any single-character mutation is caught."""
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert cmd == "npx -y skills@latest add owner/repo --global --agent claude --yes"


def test_exact_command_with_name():
    """Full-string equality including the ``--skill`` tail."""
    cmd = build_skills_install_command("owner/repo", "claude", "my-skill")
    assert cmd == (
        "npx -y skills@latest add owner/repo --global --agent claude --yes --skill my-skill"
    )


# ---------- name=None / empty-string falsy ----------


def test_name_none_omits_skill_flag():
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert "--skill" not in cmd


def test_name_empty_string_omits_skill_flag():
    """``if name:`` treats empty string as falsy, same as None. Pin
    so a mutant that swaps to ``if name is not None:`` is caught."""
    cmd = build_skills_install_command("owner/repo", "claude", "")
    assert "--skill" not in cmd
    assert cmd == "npx -y skills@latest add owner/repo --global --agent claude --yes"


def test_name_truthy_appends_skill_flag():
    cmd = build_skills_install_command("owner/repo", "claude", "my-skill")
    assert cmd.endswith("--skill my-skill")


# ---------- shlex.quote on source ----------


def test_source_with_space_is_single_quoted():
    """``shlex.quote('foo bar')`` → ``'foo bar'``. Distinguishes a
    mutant that drops the quoting (would emit a bare ``foo bar``,
    which the shell parses as two args)."""
    cmd = build_skills_install_command("foo bar", "claude", None)
    assert "'foo bar'" in cmd
    assert cmd == "npx -y skills@latest add 'foo bar' --global --agent claude --yes"


def test_source_with_command_substitution_is_quoted():
    """A source like ``$(whoami)`` MUST be single-quoted so the
    shell does not execute it. This is the security-load-bearing
    case: a mutant that drops the quoting opens a shell-injection
    sink. ``shlex.quote('$(whoami)')`` → ``'$(whoami)'``."""
    cmd = build_skills_install_command("$(whoami)", "claude", None)
    assert "'$(whoami)'" in cmd
    assert cmd == "npx -y skills@latest add '$(whoami)' --global --agent claude --yes"


def test_source_with_semicolon_is_quoted():
    """A source like ``foo;rm`` is single-quoted — semicolons would
    otherwise terminate the npx command and run a follow-up."""
    cmd = build_skills_install_command("foo;rm", "claude", None)
    assert "'foo;rm'" in cmd


def test_source_with_backtick_is_quoted():
    """Backticks would trigger command substitution if unquoted."""
    cmd = build_skills_install_command("`whoami`", "claude", None)
    assert "'`whoami`'" in cmd


# ---------- shlex.quote on agent_id ----------


def test_agent_id_with_space_is_single_quoted():
    cmd = build_skills_install_command("owner/repo", "agent name", None)
    assert "'agent name'" in cmd
    assert cmd == "npx -y skills@latest add owner/repo --global --agent 'agent name' --yes"


def test_agent_id_with_metacharacters_is_quoted():
    cmd = build_skills_install_command("owner/repo", "$(whoami)", None)
    assert "'$(whoami)'" in cmd


# ---------- shlex.quote on name ----------


def test_name_with_space_is_single_quoted():
    cmd = build_skills_install_command("owner/repo", "claude", "my skill")
    assert "--skill 'my skill'" in cmd
    assert cmd == (
        "npx -y skills@latest add owner/repo --global --agent claude --yes --skill 'my skill'"
    )


def test_name_with_metacharacters_is_quoted():
    cmd = build_skills_install_command("owner/repo", "claude", "$(rm -rf /)")
    assert "--skill '$(rm -rf /)'" in cmd


# ---------- fixed flag presence and ordering ----------


def test_command_starts_with_npx_dash_y():
    """``npx -y`` is the literal prefix — ``-y`` accepts the
    auto-install prompt non-interactively."""
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert cmd.startswith("npx -y skills@latest add ")


def test_command_includes_skills_at_latest():
    """Pin the ``skills@latest`` package spec — a mutant that drops
    the version pin or substitutes a different package name reaches
    out for the wrong CLI."""
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert "skills@latest" in cmd


def test_command_includes_add_subcommand():
    """The CLI verb is ``add`` — distinguishes a mutant that swaps
    to ``install`` or drops the verb entirely."""
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert " add " in cmd


def test_command_includes_global_flag():
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert "--global" in cmd


def test_command_includes_agent_flag():
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert "--agent" in cmd


def test_command_includes_yes_flag():
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert "--yes" in cmd


def test_flag_ordering_add_before_global():
    """``add <source>`` must come before ``--global`` so the source
    is captured as the positional argument. A reorder mutant that
    moves ``--global`` before the source breaks the CLI."""
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert cmd.index(" add ") < cmd.index("--global")


def test_flag_ordering_global_before_agent():
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert cmd.index("--global") < cmd.index("--agent")


def test_flag_ordering_agent_before_yes():
    cmd = build_skills_install_command("owner/repo", "claude", None)
    assert cmd.index("--agent") < cmd.index("--yes")


def test_skill_flag_appears_after_yes():
    """When ``name`` is supplied, ``--skill`` is appended *after*
    ``--yes`` — the function builds the base command first, then
    tacks on ``--skill``."""
    cmd = build_skills_install_command("owner/repo", "claude", "my-skill")
    assert cmd.index("--yes") < cmd.index("--skill")
