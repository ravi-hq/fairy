"""Direct unit tests for `build_provision_script`.

Mutation-tested. Each test isolates one mutation-killable branch:

  - ``set -e`` and shebang always emitted (header invariants).
  - mkdir block only when post-script writes need parent dirs.
  - chmod env file is unconditional; chmod git-creds gated on tokens.
  - Package install order respects ``PACKAGE_MANAGER_ORDER``;
    empty manager lists are skipped.
  - Git clones emitted when repos exist; credential-helper config
    emitted only when at least one repo has a token.
  - User setup script appended last when non-empty after strip.
  - Stage ordering — chmod < packages < git clones < user script.

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner) can execute them. ``SessionSpec`` is
duck-typed via ``SimpleNamespace``.
"""

from types import SimpleNamespace

from agent_on_demand.session_service.provision_script import (
    ENV_FILE_PATH,
    GIT_CREDS_PATH,
    PROVISION_SCRIPT_PATH,
    build_provision_script,
)


def _runtime(name="claude", skills_root=None):
    return SimpleNamespace(name=name, skills_root=skills_root)


def _spec(
    runtime=None,
    environment=None,
    repos=None,
    mcp_servers=None,
    skills=None,
):
    return SimpleNamespace(
        runtime=runtime if runtime is not None else _runtime(),
        environment=environment,
        repos=repos if repos is not None else [],
        mcp_servers=mcp_servers if mcp_servers is not None else [],
        skills=skills if skills is not None else [],
    )


def _env(packages=None, setup_script=""):
    return SimpleNamespace(
        packages=packages if packages is not None else {},
        setup_script=setup_script,
    )


def _repo(url="https://github.com/o/r", mount_path="/repo", token=None):
    return SimpleNamespace(url=url, mount_path=mount_path, token=token)


# ---------- header invariants ----------


def test_starts_with_bash_shebang():
    """First line must be ``#!/bin/bash`` — the file is invoked with
    ``bash -l <path>``, but the shebang is what other tools (linters,
    editors, shellcheck) key off. Pin so a refactor can't silently
    drop it."""
    script = build_provision_script(_spec())
    assert script.splitlines()[0] == "#!/bin/bash"


def test_set_e_is_emitted_on_second_line():
    """``set -e`` must be emitted before any command — without it, a
    failed apt install or git clone would be ignored and the agent
    would start in a half-provisioned state. Pin its exact position."""
    script = build_provision_script(_spec())
    assert script.splitlines()[1] == "set -e"


def test_blank_separator_after_header():
    """The header block ends with a blank separator line (``""``) so
    the rendered script visually splits set -e from the chmod block.
    Distinguishes a mutant that swaps the empty string for any
    non-empty marker."""
    lines = build_provision_script(_spec()).splitlines()
    assert lines[2] == ""


# ---------- chmod /tmp files ----------


def test_chmod_env_file_is_always_emitted():
    """The env file is always written before the script runs (even if
    empty), so chmod 600 must always fire to keep credentials off the
    other-readable bit. Pin against a refactor that gates this on
    env-vars-present."""
    script = build_provision_script(_spec())
    assert f"chmod 600 {ENV_FILE_PATH}" in script


def test_chmod_env_file_uses_600_mode_exactly():
    """600 (owner read/write only) is the mode — not 644, not 700.
    Mutmut would survive a swap to 644 without an exact-string assertion."""
    script = build_provision_script(_spec())
    lines = script.splitlines()
    assert f"chmod 600 {ENV_FILE_PATH}" in lines


def test_chmod_git_creds_omitted_when_no_repos():
    """No repos means no git creds file was written — chmod-ing a
    nonexistent file would abort under ``set -e``."""
    script = build_provision_script(_spec())
    assert GIT_CREDS_PATH not in script


def test_chmod_git_creds_omitted_when_repos_have_no_tokens():
    """Public repos don't get tokens; the git-creds file isn't written,
    so don't chmod it. Distinguishes a mutant that drops the
    ``any(r.token for r in spec.repos)`` guard."""
    spec = _spec(repos=[_repo(token=None)])
    script = build_provision_script(spec)
    assert f"chmod 600 {GIT_CREDS_PATH}" not in script


def test_chmod_git_creds_emitted_when_any_repo_has_token():
    """At least one tokened repo means the git-creds file exists and
    needs chmod. Use a single tokened repo to keep the case minimal."""
    spec = _spec(repos=[_repo(token="ghp_abc")])
    script = build_provision_script(spec)
    assert f"chmod 600 {GIT_CREDS_PATH}" in script


def test_chmod_git_creds_emitted_when_mixed_token_and_public():
    """Mixed repos: as long as ANY has a token, chmod fires.
    Distinguishes a mutant that swaps ``any`` → ``all``."""
    spec = _spec(repos=[_repo(token=None), _repo(token="ghp_abc")])
    script = build_provision_script(spec)
    assert f"chmod 600 {GIT_CREDS_PATH}" in script


# ---------- mkdir block ----------


def test_no_mkdir_when_no_post_script_writes():
    """No MCP servers, no inline skills → no mkdir line. Distinguishes
    a mutant that emits the mkdir prefix unconditionally."""
    script = build_provision_script(_spec())
    assert "mkdir -p" not in script


def test_mkdir_emitted_for_codex_mcp_dir():
    """Codex MCP config is written after the script runs, so its
    parent must be pre-created. Pin the exact path so a refactor that
    moves codex config can't silently regress."""
    spec = _spec(
        runtime=_runtime(name="codex"),
        mcp_servers=[SimpleNamespace(name="srv")],
    )
    script = build_provision_script(spec)
    assert "mkdir -p /home/sprite/.codex" in script


def test_mkdir_joins_multiple_dirs_with_single_space():
    """When two dirs need pre-creation (e.g. codex MCP + an inline
    skill), they go on a single ``mkdir -p`` line separated by one
    space. Distinguishes a mutant that swaps the join separator."""
    runtime = _runtime(name="codex", skills_root="/skills")
    skill = SimpleNamespace(name="hello", content="x", source=None)
    spec = _spec(
        runtime=runtime,
        mcp_servers=[SimpleNamespace(name="srv")],
        skills=[skill],
    )
    script = build_provision_script(spec)
    assert "mkdir -p /home/sprite/.codex /skills/hello" in script


def test_blank_separator_after_mkdir_block():
    """A blank line follows the mkdir block to visually separate it
    from the chmod block. Distinguishes a ``"" → "XXXX"`` mutant on
    the trailing append."""
    spec = _spec(
        runtime=_runtime(name="codex"),
        mcp_servers=[SimpleNamespace(name="srv")],
    )
    lines = build_provision_script(spec).splitlines()
    mkdir_idx = next(i for i, line in enumerate(lines) if line.startswith("mkdir -p"))
    assert lines[mkdir_idx + 1] == ""


def test_mkdir_quotes_paths_with_spaces():
    """Skill names can come from user input; defense-in-depth quoting.
    A name with a space in it must land single-quoted so the mkdir
    arg list doesn't split. Validators upstream block this, but the
    quoting pins behavior at this layer too."""
    runtime = _runtime(name="claude", skills_root="/skills")
    skill = SimpleNamespace(name="my skill", content="x", source=None)
    script = build_provision_script(_spec(runtime=runtime, skills=[skill]))
    assert "mkdir -p '/skills/my skill'" in script


# ---------- packages ----------


def test_no_packages_block_when_env_is_none():
    """No environment → no packages section. Distinguishes a mutant
    that drops the ``env and env.packages`` guard."""
    script = build_provision_script(_spec(environment=None))
    assert "apt-get" not in script
    assert "pip install" not in script


def test_no_packages_block_when_env_has_no_packages():
    """An environment with packages={} should not emit any install
    command. Pin against a mutant that swaps the truthiness check."""
    script = build_provision_script(_spec(environment=_env(packages={})))
    assert "apt-get" not in script
    assert "pip install" not in script


def test_apt_install_command_emitted_when_apt_packages_present():
    spec = _spec(environment=_env(packages={"apt": ["curl"]}))
    script = build_provision_script(spec)
    assert "apt-get update -qq && apt-get install -y curl" in script


def test_apt_runs_before_pip_in_emitted_order():
    """apt must run first because pip-installable packages may need
    apt-supplied native libraries (libssl, libpq) on PATH first.
    Distinguishes a mutant that iterates dict insertion order or
    sorts alphabetically (apt → cargo → … → pip would still pass an
    'apt is index 0' assertion but that's the order we want)."""
    spec = _spec(environment=_env(packages={"pip": ["requests"], "apt": ["curl"]}))
    script = build_provision_script(spec)
    apt_idx = script.find("apt-get update")
    pip_idx = script.find("pip install")
    assert apt_idx > -1
    assert pip_idx > -1
    assert apt_idx < pip_idx


def test_managers_with_empty_lists_are_skipped():
    """A manager key with an empty package list must produce no
    command. Distinguishes a mutant that drops the ``if not pkgs:
    continue`` guard — an empty apt-get install line would crash
    under ``set -e``."""
    spec = _spec(environment=_env(packages={"apt": [], "pip": ["requests"]}))
    script = build_provision_script(spec)
    assert "apt-get" not in script
    assert "pip install requests" in script


def test_packages_section_ends_with_trailing_newline():
    """The packages section is followed by a blank separator line so
    when packages is the last section emitted, the script ends in
    ``\\n``. Distinguishes a mutant that swaps the trailing ``""``
    append for a non-empty marker."""
    spec = _spec(environment=_env(packages={"apt": ["curl"]}))
    script = build_provision_script(spec)
    assert script.endswith("apt-get update -qq && apt-get install -y curl\n")


def test_all_six_managers_emit_in_canonical_order():
    """Full coverage: every manager has packages, all six commands
    appear in PACKAGE_MANAGER_ORDER order. Line-prefix anchors avoid
    matching ``go install`` as a suffix of ``cargo install``."""
    spec = _spec(
        environment=_env(
            packages={
                "pip": ["a"],
                "go": ["github.com/o/g"],
                "apt": ["b"],
                "gem": ["c"],
                "cargo": ["d"],
                "npm": ["e"],
            }
        )
    )
    lines = build_provision_script(spec).splitlines()
    install_lines = [
        i
        for i, line in enumerate(lines)
        if line.startswith(
            ("apt-get", "cargo install", "gem install", "go install", "npm install", "pip install")
        )
    ]
    prefixes = [lines[i].split()[0] for i in install_lines]
    # apt's line starts with "apt-get", others with the manager name.
    assert prefixes == ["apt-get", "cargo", "gem", "go", "npm", "pip"]


# ---------- git clones ----------


def test_no_git_section_when_no_repos():
    """No repos → no clone, no credential helper config. Distinguishes
    a mutant that emits the credential-helper unconditionally."""
    script = build_provision_script(_spec())
    assert "git clone" not in script
    assert "credential.helper" not in script


def test_clone_emitted_for_each_repo():
    spec = _spec(
        repos=[
            _repo(url="https://github.com/o/a", mount_path="/a"),
            _repo(url="https://github.com/o/b", mount_path="/b"),
        ],
    )
    script = build_provision_script(spec)
    assert "git clone --depth=1 --quiet https://github.com/o/a /a" in script
    assert "git clone --depth=1 --quiet https://github.com/o/b /b" in script


def test_clone_uses_depth_1_for_quick_provisioning():
    """``--depth=1`` keeps the clone fast; full history is irrelevant
    for an agent session. Pin the flag against a mutant that drops it."""
    spec = _spec(repos=[_repo()])
    script = build_provision_script(spec)
    assert "--depth=1" in script


def test_clone_uses_quiet_to_keep_provision_log_readable():
    """``--quiet`` suppresses progress output that would otherwise
    flood the SSE provision stream. Pin the flag."""
    spec = _spec(repos=[_repo()])
    script = build_provision_script(spec)
    assert "--quiet" in script


def test_clone_shell_quotes_url_and_mount_path():
    """Defense-in-depth shell-quoting on URL + path. Validators upstream
    reject metachar payloads, but the quoting pins behavior at this
    layer."""
    spec = _spec(
        repos=[_repo(url="https://x/a;rm", mount_path="/m;rm")],
    )
    script = build_provision_script(spec)
    assert "'https://x/a;rm'" in script
    assert "'/m;rm'" in script


def test_credential_helper_omitted_when_no_repo_has_token():
    """Public-only repos → no credential helper config. Distinguishes
    a mutant that drops the ``any(r.token …)`` guard."""
    spec = _spec(repos=[_repo(token=None), _repo(token=None)])
    script = build_provision_script(spec)
    assert "credential.helper" not in script


def test_credential_helper_emitted_when_any_repo_has_token():
    spec = _spec(repos=[_repo(token="ghp_abc")])
    script = build_provision_script(spec)
    assert f"git config --global credential.helper 'store --file={GIT_CREDS_PATH}'" in script


def test_git_clone_section_ends_with_trailing_newline():
    """The git clone section is followed by a blank separator line so
    when clones are the last section emitted, the script ends in
    ``\\n``. Distinguishes a mutant that swaps the trailing ``""``
    append for a non-empty marker."""
    spec = _spec(repos=[_repo(url="https://x/r", mount_path="/r")])
    script = build_provision_script(spec)
    assert script.endswith("git clone --depth=1 --quiet https://x/r /r\n")


def test_credential_helper_runs_before_clones():
    """The helper must be configured BEFORE the clone runs, otherwise
    the clone falls back to interactive auth and hangs the script.
    Pin the order."""
    spec = _spec(repos=[_repo(token="ghp_abc")])
    script = build_provision_script(spec)
    helper_idx = script.find("credential.helper")
    clone_idx = script.find("git clone")
    assert helper_idx > -1
    assert clone_idx > -1
    assert helper_idx < clone_idx


# ---------- user setup script ----------


def test_user_setup_script_appended_when_non_empty():
    spec = _spec(environment=_env(setup_script="echo hello"))
    script = build_provision_script(spec)
    assert "echo hello" in script


def test_user_setup_script_skipped_when_environment_is_none():
    """No environment → no user script. Distinguishes a mutant that
    drops the ``env is not None`` guard."""
    script = build_provision_script(_spec(environment=None))
    assert "echo " not in script


def test_user_setup_script_skipped_when_empty_string():
    """Empty setup_script → no extra line. Pin against a mutant that
    emits a stray empty line as the script."""
    script = build_provision_script(_spec(environment=_env(setup_script="")))
    # No user content beyond the standard header / chmod.
    assert "echo " not in script


def test_user_setup_script_skipped_when_only_whitespace():
    """``"   \n\t  "``.strip() is empty — must be skipped, not run.
    Distinguishes a mutant that drops the ``.strip()`` call."""
    script = build_provision_script(_spec(environment=_env(setup_script="  \n\t  ")))
    # Whitespace-only setup_script must not produce a setup line — last
    # non-blank line is the chmod from the standard header.
    lines = script.strip().splitlines()
    assert lines[-1].startswith("chmod ")


def test_user_setup_script_when_none_produces_no_extra_lines():
    """A None setup_script must not coerce through ``or "X"`` into a
    non-empty literal. Distinguishes a mutant that swaps the ``or ""``
    fallback for a non-empty default — the mutant would inject the
    fallback string as the user's setup script."""
    env = SimpleNamespace(packages={}, setup_script=None)
    with_env = build_provision_script(_spec(environment=env))
    no_env = build_provision_script(_spec(environment=None))
    assert with_env == no_env


def test_user_setup_script_followed_by_trailing_newline():
    """When a setup script is appended, an empty trailing line follows
    so the rendered script ends with ``\\n``. Distinguishes a mutant
    that swaps the trailing ``""`` for a non-empty marker (which would
    leave the script ending without a newline)."""
    spec = _spec(environment=_env(setup_script="echo hello"))
    script = build_provision_script(spec)
    assert script.endswith("\n")
    # And specifically: ends with the setup script + newline, not
    # the setup script + a non-empty marker.
    assert script.endswith("echo hello\n")


def test_user_setup_script_runs_after_packages_and_clones():
    """Setup script comes last so it runs in an environment where
    packages and repos are already in place. Pin the ordering."""
    spec = _spec(
        environment=_env(
            packages={"apt": ["curl"]},
            setup_script="echo done",
        ),
        repos=[_repo(url="https://x/a", mount_path="/a")],
    )
    script = build_provision_script(spec)
    apt_idx = script.find("apt-get")
    clone_idx = script.find("git clone")
    user_idx = script.find("echo done")
    assert apt_idx > -1
    assert clone_idx > -1
    assert user_idx > -1
    assert apt_idx < user_idx
    assert clone_idx < user_idx


# ---------- stage ordering invariants ----------


def test_chmod_runs_before_packages():
    """chmod the env file before any install — apt may read env
    settings (proxies, etc) and a 644 env file would leak."""
    spec = _spec(environment=_env(packages={"apt": ["curl"]}))
    script = build_provision_script(spec)
    chmod_idx = script.find("chmod 600")
    apt_idx = script.find("apt-get")
    assert chmod_idx > -1
    assert apt_idx > -1
    assert chmod_idx < apt_idx


def test_packages_run_before_git_clones():
    """Apt may install git itself or git's TLS deps — package install
    must run before clones."""
    spec = _spec(
        environment=_env(packages={"apt": ["git"]}),
        repos=[_repo()],
    )
    script = build_provision_script(spec)
    apt_idx = script.find("apt-get")
    clone_idx = script.find("git clone")
    assert apt_idx > -1
    assert clone_idx > -1
    assert apt_idx < clone_idx


def test_mkdir_runs_before_chmod():
    """mkdir runs first so any post-script writes (codex MCP, skill
    dirs) have parents in place — chmod-ing the env file later is
    independent, but the spec.runtime.skills_root mkdir must precede
    any further work that touches /home/sprite."""
    spec = _spec(
        runtime=_runtime(name="codex"),
        mcp_servers=[SimpleNamespace(name="srv")],
    )
    script = build_provision_script(spec)
    mkdir_idx = script.find("mkdir -p")
    chmod_idx = script.find("chmod 600")
    assert mkdir_idx > -1
    assert chmod_idx > -1
    assert mkdir_idx < chmod_idx


# ---------- path constants ----------


def test_env_file_path_lives_in_tmp():
    """The env file lives in /tmp on the Sprite VM — the dispatcher
    sources it from this exact path. Pin the constant."""
    assert ENV_FILE_PATH == "/tmp/aod-env"


def test_git_creds_path_lives_in_tmp():
    """The git creds file lives in /tmp; the credential helper config
    references this exact path. Pin the constant."""
    assert GIT_CREDS_PATH == "/tmp/.git-credentials"


def test_provision_script_path_lives_in_tmp():
    """The provision script lives at /tmp/aod-provision.sh — invoked
    by ``sprite.command("bash", "-l", PROVISION_SCRIPT_PATH)``."""
    assert PROVISION_SCRIPT_PATH == "/tmp/aod-provision.sh"
