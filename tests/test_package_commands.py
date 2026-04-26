"""Direct unit tests for `package_commands` and `PACKAGE_MANAGER_ORDER`.

Mutation-tested. Each test isolates one mutation-killable branch:

  - Per-manager dispatch (apt / cargo / gem / go / npm / pip)
  - apt batches: ``apt-get update -qq && apt-get install -y …``
  - pip / npm / gem batch into one call
  - cargo / go install one binary at a time (per-package noise control)
  - shlex.quote on every package name — pinned with metacharacters
  - Unknown manager raises ValueError (mirrors the loud-failure pattern
    from #175)
  - PACKAGE_MANAGER_ORDER set + ordering

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner) can execute them.
"""

import pytest

from agent_on_demand.session_service.package_commands import (
    PACKAGE_MANAGER_ORDER,
    package_commands,
)


# ---------- per-manager command shape ----------


def test_apt_batches_update_and_install_in_one_command():
    """apt update + install runs as a single ``&&``-chained command —
    one round-trip in the provision shell. Pinned because splitting
    them would land update + install in separate ``RUN`` lines and
    silently drop the apt cache between them."""
    cmds = package_commands("apt", ["curl", "git"])
    assert cmds == ["apt-get update -qq && apt-get install -y curl git"]


def test_apt_uses_dash_y_to_skip_prompts():
    """Without ``-y``, apt-get install prompts for confirmation and
    hangs the headless provision shell forever. Pin the flag."""
    cmds = package_commands("apt", ["pkg"])
    assert "-y" in cmds[0]


def test_apt_uses_qq_for_quiet_update():
    """``apt-get update -qq`` keeps the provision log readable —
    without it every package list refresh dumps hundreds of lines into
    the session output stream."""
    cmds = package_commands("apt", ["pkg"])
    assert "-qq" in cmds[0]


def test_pip_batches_packages_in_one_call():
    """All pip packages install in one call so pip can resolve the
    dependency graph holistically. Pinned so a refactor that loops
    one-per-call doesn't silently regress install time."""
    cmds = package_commands("pip", ["requests", "flask"])
    assert cmds == ["pip install requests flask"]


def test_npm_install_is_global_flag():
    """``--global`` is required for the agent's PATH to pick the binary
    up. Without ``-g``, npm installs into a node_modules dir that
    isn't on PATH and the binary isn't callable from the agent."""
    cmds = package_commands("npm", ["typescript"])
    assert cmds == ["npm install --global typescript"]


def test_npm_batches_packages_in_one_call():
    cmds = package_commands("npm", ["pkg-a", "pkg-b"])
    assert cmds == ["npm install --global pkg-a pkg-b"]


def test_gem_batches_packages_in_one_call():
    cmds = package_commands("gem", ["bundler", "rails"])
    assert cmds == ["gem install bundler rails"]


# ---------- per-binary install (cargo / go) ----------


def test_cargo_install_one_per_package():
    """cargo install fails noisily when one of N packages fails to
    compile; running them one-at-a-time keeps the failure attributable
    to its package. Pinned so a "batch them all" refactor breaks this
    test."""
    cmds = package_commands("cargo", ["ripgrep", "fd-find"])
    assert cmds == ["cargo install ripgrep", "cargo install fd-find"]


def test_go_install_one_per_package():
    cmds = package_commands("go", ["github.com/owner/a", "github.com/owner/b"])
    assert cmds == [
        "go install github.com/owner/a",
        "go install github.com/owner/b",
    ]


# ---------- shell quoting (defense-in-depth) ----------


def test_pip_shell_quotes_names_with_spaces():
    """Package names with shell metacharacters are quoted via
    ``shlex.quote``. Defense-in-depth — the API validator should reject
    these names, but a refactor that drops the quoting would let an
    edge-case payload land verbatim in the bash script."""
    cmds = package_commands("pip", ["pack age", "ok"])
    # shlex.quote wraps the spaceful one in single quotes.
    assert cmds == ["pip install 'pack age' ok"]


def test_cargo_shell_quotes_names_with_metacharacters():
    cmds = package_commands("cargo", ["pkg;rm -rf /"])
    # Single-quoted, semicolon and slash inside.
    assert cmds == ["cargo install 'pkg;rm -rf /'"]


def test_apt_shell_quotes_names_with_special_chars():
    """Defense-in-depth at the install layer too — the API validator
    rejects these but a missed validation would otherwise let a
    metachar payload land in apt-get."""
    cmds = package_commands("apt", ["pkg`whoami`"])
    assert "'pkg`whoami`'" in cmds[0]


def test_npm_shell_quotes_names_with_metacharacters():
    """Per-manager metachar coverage — a refactor that drops quoting on
    only one manager (e.g. swapping `_join_quoted` for raw join) must
    fail at least one test per branch, not just apt/pip/cargo."""
    cmds = package_commands("npm", ["pkg;rm -rf /", "ok"])
    assert cmds == ["npm install --global 'pkg;rm -rf /' ok"]


def test_gem_shell_quotes_names_with_metacharacters():
    cmds = package_commands("gem", ["pkg`whoami`", "ok"])
    assert cmds == ["gem install 'pkg`whoami`' ok"]


def test_go_shell_quotes_names_with_metacharacters():
    """go install runs one-per-package, so each command gets its own
    shlex.quote call — pin both the per-package shape and the quoting."""
    cmds = package_commands("go", ["github.com/owner/pkg;evil", "github.com/owner/ok"])
    assert cmds == [
        "go install 'github.com/owner/pkg;evil'",
        "go install github.com/owner/ok",
    ]


# ---------- unknown-manager handling ----------


def test_unknown_manager_raises_value_error():
    """Defense-in-depth: a future ``ORDER`` addition without a
    dispatch branch would silently drop those packages today. Loud
    failure instead. Mirrors the pattern from #175."""
    with pytest.raises(ValueError, match="Unsupported package manager: 'yarn'"):
        package_commands("yarn", ["pkg"])


def test_empty_string_manager_raises_value_error():
    """Edge case: ``""`` isn't apt/cargo/etc, so it lands in the
    raise branch — distinguishes a mutant that swaps the if-chain for
    an unconditional dispatch."""
    with pytest.raises(ValueError):
        package_commands("", ["pkg"])


def test_unknown_manager_includes_name_in_error():
    """The error names the offending manager so the operator can find
    the misconfiguration. Pinned since mutmut would otherwise survive
    a swap to a generic ``ValueError("bad manager")``."""
    with pytest.raises(ValueError, match="ghost-manager"):
        package_commands("ghost-manager", ["pkg"])


# ---------- PACKAGE_MANAGER_ORDER constant ----------


def test_order_contains_exactly_the_six_supported_managers():
    """The ORDER set is the source of truth for which managers
    ``_build_provision_script`` will iterate. Pinned so a refactor
    can't quietly add or drop a manager (which would also have to
    update ``package_commands``'s dispatch — the constraint that
    matters)."""
    assert set(PACKAGE_MANAGER_ORDER) == {"apt", "cargo", "gem", "go", "npm", "pip"}


def test_order_starts_with_apt():
    """apt comes first because language-level managers (cargo, gem,
    go, npm, pip) often need apt-installed libraries (libpq, libssl,
    libxml2, etc.) on PATH before they can build native extensions.
    Pin so a re-ordering refactor doesn't break network-dependent
    builds in subtle ways."""
    assert PACKAGE_MANAGER_ORDER[0] == "apt"


def test_order_is_a_tuple_not_a_list():
    """ORDER is a tuple so it's hashable and immutable — accidental
    mutation by a caller wouldn't change behavior elsewhere. Pin
    so a refactor to ``list[str]`` is caught."""
    assert isinstance(PACKAGE_MANAGER_ORDER, tuple)
