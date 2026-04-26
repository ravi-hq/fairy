"""Generate a report from mutmut output, in HTML or Markdown.

Reads `mutants/src/**/*.py.meta` and `mutants/mutmut-cicd-stats.json`.

`--format=html` (default) writes a self-contained `mutants/report.html` with
top-line stats, per-file/per-function kill-rate heatmap, and the unified diff
of every surviving mutant.

`--format=markdown` prints a PR-comment-friendly summary to stdout.

Both modes flag known-equivalent survivors using the allowlist in
`scripts.check_mutmut`.

Assumes `make mutation-test` (or `mutmut run`) has already populated `mutants/`.

Run: `uv run python -m scripts.mutmut_report [--format html|markdown]`
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from scripts.check_mutmut import KNOWN_EQUIVALENT

REPO_ROOT = Path(__file__).resolve().parent.parent
MUTANTS_DIR = REPO_ROOT / "mutants"
CICD_STATS_PATH = MUTANTS_DIR / "mutmut-cicd-stats.json"
REPORT_PATH = MUTANTS_DIR / "report.html"

# mutmut exit codes (per mutmut.__main__): 0=survived, 1=killed,
# 2=timeout, 3=suspicious, 4=skipped, 5=no_tests.
EXIT_KILLED = 1
EXIT_SURVIVED = 0
EXIT_TIMEOUT = 2
EXIT_SUSPICIOUS = 3


@dataclass
class Mutant:
    mutant_id: str  # e.g. agent_on_demand.auth.x__check_api_key_sync__mutmut_8
    module: str  # agent_on_demand.auth
    func: str  # _check_api_key_sync (un-mangled)
    exit_code: int
    duration: float | None

    @property
    def status(self) -> str:
        return {
            EXIT_KILLED: "killed",
            EXIT_SURVIVED: "survived",
            EXIT_TIMEOUT: "timeout",
            EXIT_SUSPICIOUS: "suspicious",
        }.get(self.exit_code, f"exit{self.exit_code}")


@dataclass
class FuncBucket:
    module: str
    func: str
    mutants: list[Mutant] = field(default_factory=list)

    @property
    def killed(self) -> int:
        return sum(1 for m in self.mutants if m.exit_code == EXIT_KILLED)

    @property
    def survived(self) -> int:
        return sum(1 for m in self.mutants if m.exit_code == EXIT_SURVIVED)

    @property
    def kill_rate(self) -> float:
        return self.killed / len(self.mutants) if self.mutants else 0.0


@dataclass
class FileBucket:
    source_path: str  # src/agent_on_demand/auth.py
    funcs: dict[str, FuncBucket] = field(default_factory=dict)

    @property
    def all_mutants(self) -> list[Mutant]:
        return [m for fb in self.funcs.values() for m in fb.mutants]

    @property
    def killed(self) -> int:
        return sum(fb.killed for fb in self.funcs.values())

    @property
    def survived(self) -> int:
        return sum(fb.survived for fb in self.funcs.values())

    @property
    def total(self) -> int:
        return sum(len(fb.mutants) for fb in self.funcs.values())

    @property
    def kill_rate(self) -> float:
        return self.killed / self.total if self.total else 0.0


def _parse_mutant_id(mutant_id: str) -> tuple[str, str]:
    """('agent_on_demand.auth.x__check_api_key_sync__mutmut_8',) → (module, func)."""
    mangled, _idx = mutant_id.rsplit("__mutmut_", 1)
    module, func_mangled = mangled.rsplit(".", 1)
    # mutmut prefixes function names with `x_` (so `_foo` → `x__foo`, `foo` → `x_foo`)
    func = func_mangled[2:] if func_mangled.startswith("x_") else func_mangled
    return module, func


def _meta_to_source_path(meta_path: Path) -> str:
    """mutants/src/agent_on_demand/auth.py.meta → src/agent_on_demand/auth.py"""
    rel = meta_path.relative_to(MUTANTS_DIR)
    return str(rel)[: -len(".meta")]


def _collect() -> tuple[dict[str, FileBucket], dict[str, int]]:
    files: dict[str, FileBucket] = {}
    totals: dict[str, int] = defaultdict(int)
    for meta_path in sorted(MUTANTS_DIR.rglob("*.py.meta")):
        meta = json.loads(meta_path.read_text())
        source_path = _meta_to_source_path(meta_path)
        bucket = FileBucket(source_path=source_path)
        durations = meta.get("durations_by_key", {})
        for mutant_id, exit_code in meta["exit_code_by_key"].items():
            module, func = _parse_mutant_id(mutant_id)
            mutant = Mutant(
                mutant_id=mutant_id,
                module=module,
                func=func,
                exit_code=exit_code,
                duration=durations.get(mutant_id),
            )
            bucket.funcs.setdefault(func, FuncBucket(module=module, func=func)).mutants.append(
                mutant
            )
            totals[mutant.status] += 1
            totals["total"] += 1
        files[source_path] = bucket
    return files, dict(totals)


def _diff_for(mutant_id: str) -> str:
    """Run `mutmut show` and return only the diff portion (strip headers)."""
    out = subprocess.run(
        ["uv", "run", "mutmut", "show", mutant_id],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    text = out.stdout
    # Strip uv build noise + the `# mutant_id: status` header line.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("--- "):
            return "\n".join(lines[i:])
    return text


def _heat_color(rate: float) -> str:
    """Green at 1.0, yellow at 0.85, red at 0.5 and below."""
    if rate >= 0.99:
        return "#1f9d55"
    if rate >= 0.95:
        return "#3da76e"
    if rate >= 0.85:
        return "#d4a017"
    if rate >= 0.7:
        return "#d97706"
    return "#b91c1c"


def _render_html(files: dict[str, FileBucket], totals: dict[str, int]) -> str:
    cicd = json.loads(CICD_STATS_PATH.read_text()) if CICD_STATS_PATH.exists() else {}
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    total = totals.get("total", 0)
    killed = totals.get("killed", 0)
    survived = totals.get("survived", 0)
    timeout = totals.get("timeout", 0)
    suspicious = totals.get("suspicious", 0)
    kill_rate = killed / total if total else 0.0

    survivors = [
        m
        for fb in files.values()
        for func in fb.funcs.values()
        for m in func.mutants
        if m.exit_code == EXIT_SURVIVED
    ]

    parts: list[str] = []
    parts.append(_HEAD)
    parts.append("<h1>Mutation testing report</h1>")
    parts.append(
        f'<p class="meta">Generated {html.escape(generated_at)} from <code>mutants/</code></p>'
    )

    # Top-line stats
    parts.append('<section class="topline">')
    parts.append(_stat_card("Kill rate", f"{kill_rate:.1%}", _heat_color(kill_rate)))
    parts.append(_stat_card("Killed", str(killed), "#1f9d55"))
    parts.append(_stat_card("Survived", str(survived), "#b91c1c"))
    if timeout:
        parts.append(_stat_card("Timeout", str(timeout), "#d97706"))
    if suspicious:
        parts.append(_stat_card("Suspicious", str(suspicious), "#d97706"))
    parts.append(_stat_card("Total", str(total), "#444"))
    parts.append("</section>")

    if cicd and cicd != {"killed": killed, "survived": survived, "total": total}:
        # Sanity check that our parse matches mutmut's own stats. If it doesn't,
        # surface it — silent divergence would be worse than a noisy mismatch.
        diff = {k: cicd.get(k) for k in ("killed", "survived", "timeout", "suspicious", "total")}
        ours = {
            "killed": killed,
            "survived": survived,
            "timeout": timeout,
            "suspicious": suspicious,
            "total": total,
        }
        if diff != ours:
            parts.append(
                f'<p class="warn">⚠ stats mismatch with mutmut-cicd-stats.json — '
                f"mutmut says {html.escape(json.dumps(diff))}, "
                f"parsed {html.escape(json.dumps(ours))}</p>"
            )

    # Per-file table
    parts.append("<h2>By file</h2>")
    parts.append('<table class="files">')
    parts.append(
        "<thead><tr><th>File</th><th class='num'>Killed</th>"
        "<th class='num'>Survived</th><th class='num'>Total</th>"
        "<th>Kill rate</th></tr></thead><tbody>"
    )
    for path, fb in sorted(files.items()):
        parts.append(
            f"<tr><td><code>{html.escape(path)}</code></td>"
            f"<td class='num'>{fb.killed}</td>"
            f"<td class='num'>{fb.survived}</td>"
            f"<td class='num'>{fb.total}</td>"
            f"<td>{_bar(fb.kill_rate)}</td></tr>"
        )
    parts.append("</tbody></table>")

    # Per-function (collapsible per file)
    parts.append("<h2>By function</h2>")
    for path, fb in sorted(files.items()):
        parts.append(
            f"<details open><summary><code>{html.escape(path)}</code> "
            f"<span class='muted'>— {fb.killed}/{fb.total} killed</span></summary>"
        )
        parts.append('<table class="funcs">')
        parts.append(
            "<thead><tr><th>Function</th><th class='num'>Killed</th>"
            "<th class='num'>Survived</th><th class='num'>Total</th>"
            "<th>Kill rate</th></tr></thead><tbody>"
        )
        for func_name in sorted(fb.funcs):
            func = fb.funcs[func_name]
            parts.append(
                f"<tr><td><code>{html.escape(func.func)}</code></td>"
                f"<td class='num'>{func.killed}</td>"
                f"<td class='num'>{func.survived}</td>"
                f"<td class='num'>{len(func.mutants)}</td>"
                f"<td>{_bar(func.kill_rate)}</td></tr>"
            )
        parts.append("</tbody></table></details>")

    # Surviving mutants
    parts.append(f"<h2>Surviving mutants ({len(survivors)})</h2>")
    if not survivors:
        parts.append('<p class="muted">None — every mutant was killed.</p>')
    for mutant in sorted(survivors, key=lambda m: m.mutant_id):
        equivalent = mutant.mutant_id in KNOWN_EQUIVALENT
        badge = (
            '<span class="badge ok">known-equivalent</span>'
            if equivalent
            else '<span class="badge bad">survived</span>'
        )
        diff = _diff_for(mutant.mutant_id)
        parts.append(
            f"<details {'' if equivalent else 'open'}>"
            f"<summary><code>{html.escape(mutant.mutant_id)}</code> {badge}</summary>"
            f'<pre class="diff">{_color_diff(diff)}</pre>'
            f"</details>"
        )

    parts.append("</body></html>")
    return "\n".join(parts)


def _stat_card(label: str, value: str, color: str) -> str:
    return (
        f'<div class="card" style="border-color:{color}">'
        f'<div class="card-value" style="color:{color}">{html.escape(value)}</div>'
        f'<div class="card-label">{html.escape(label)}</div></div>'
    )


def _bar(rate: float) -> str:
    pct = round(rate * 100, 1)
    color = _heat_color(rate)
    return (
        f'<div class="bar"><div class="bar-fill" '
        f'style="width:{pct}%;background:{color}"></div>'
        f'<span class="bar-label">{pct}%</span></div>'
    )


def _color_diff(diff: str) -> str:
    out: list[str] = []
    for line in diff.splitlines():
        esc = html.escape(line)
        if line.startswith("+++") or line.startswith("---"):
            out.append(f'<span class="diff-meta">{esc}</span>')
        elif line.startswith("@@"):
            out.append(f'<span class="diff-hunk">{esc}</span>')
        elif line.startswith("+"):
            out.append(f'<span class="diff-add">{esc}</span>')
        elif line.startswith("-"):
            out.append(f'<span class="diff-del">{esc}</span>')
        else:
            out.append(esc)
    return "\n".join(out)


_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Mutation testing report</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color: #222; line-height: 1.5; }
  h1 { margin-bottom: 0.25rem; }
  h2 { margin-top: 2.5rem; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; }
  .meta { color: #666; margin-top: 0; }
  .muted { color: #888; font-weight: normal; }
  .warn { background: #fff3cd; padding: 0.5rem 0.75rem; border-left: 3px solid #d97706; }
  .topline { display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 1.5rem 0; }
  .card { border: 2px solid #ccc; border-radius: 6px; padding: 0.75rem 1rem; min-width: 110px; }
  .card-value { font-size: 1.6rem; font-weight: 600; line-height: 1.1; }
  .card-label { font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.04em; }
  table { width: 100%; border-collapse: collapse; margin: 0.5rem 0 1.5rem; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #eee; }
  th { background: #fafafa; font-weight: 600; font-size: 0.85rem; color: #555; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 0.9em; }
  .bar { position: relative; background: #eee; border-radius: 3px; height: 18px; min-width: 200px; }
  .bar-fill { height: 100%; border-radius: 3px; }
  .bar-label { position: absolute; right: 6px; top: 0; line-height: 18px; font-size: 0.75rem;
               color: #222; font-variant-numeric: tabular-nums; }
  details { margin: 0.5rem 0; }
  summary { cursor: pointer; padding: 0.25rem 0; font-weight: 500; }
  .badge { display: inline-block; padding: 0.05rem 0.5rem; border-radius: 3px; font-size: 0.75rem;
           font-weight: 600; margin-left: 0.5rem; vertical-align: middle; }
  .badge.ok { background: #def7e3; color: #1f7a3a; }
  .badge.bad { background: #fde2e2; color: #9a1d1d; }
  pre.diff { background: #fafafa; padding: 0.75rem 1rem; border-radius: 4px; overflow-x: auto;
             font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 0.85rem;
             line-height: 1.45; }
  .diff-add { color: #1f7a3a; background: #e7f7ec; display: inline-block; width: 100%; }
  .diff-del { color: #9a1d1d; background: #fde7e7; display: inline-block; width: 100%; }
  .diff-meta { color: #666; }
  .diff-hunk { color: #6f42c1; }
</style>
</head><body>
"""


def _render_markdown(files: dict[str, FileBucket], totals: dict[str, int]) -> str:
    total = totals.get("total", 0)
    killed = totals.get("killed", 0)
    survived = totals.get("survived", 0)
    timeout = totals.get("timeout", 0)
    suspicious = totals.get("suspicious", 0)
    kill_rate = killed / total if total else 0.0

    survivors = [
        m
        for fb in files.values()
        for func in fb.funcs.values()
        for m in func.mutants
        if m.exit_code == EXIT_SURVIVED
    ]
    new_survivors = [m for m in survivors if m.mutant_id not in KNOWN_EQUIVALENT]

    if new_survivors or timeout or suspicious:
        emoji = "❌"
    elif survivors:
        emoji = "✅"  # only known-equivalents
    else:
        emoji = "✅"

    lines: list[str] = []
    lines.append("<!-- mutation-report -->")
    lines.append("## 🧬 Mutation testing report")
    lines.append("")

    headline = f"{emoji} **{kill_rate:.1%} kill rate** — {killed} killed, {survived} survived"
    extras = []
    if timeout:
        extras.append(f"{timeout} timeout")
    if suspicious:
        extras.append(f"{suspicious} suspicious")
    if extras:
        headline += " (" + ", ".join(extras) + ")"
    headline += f", {total} total."
    lines.append(headline)
    if survivors and not new_survivors:
        lines.append("")
        lines.append(f"All {len(survivors)} survivors are documented known-equivalents.")
    elif new_survivors:
        lines.append("")
        lines.append(
            f"⚠️ **{len(new_survivors)} new surviving mutant(s)** — "
            f"see [`scripts/check_mutmut.py`](../blob/main/scripts/check_mutmut.py) "
            "for the equivalent-mutant allowlist."
        )
    lines.append("")

    lines.append("### By file")
    lines.append("")
    lines.append("| File | Killed | Survived | Total | Rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for path, fb in sorted(files.items()):
        lines.append(
            f"| `{path}` | {fb.killed} | {fb.survived} | {fb.total} | {fb.kill_rate:.1%} |"
        )
    lines.append("")

    lines.append(f"### Surviving mutants ({len(survivors)})")
    lines.append("")
    if not survivors:
        lines.append("_None — every mutant was killed._")
        return "\n".join(lines) + "\n"

    for mutant in sorted(survivors, key=lambda m: m.mutant_id):
        equivalent = mutant.mutant_id in KNOWN_EQUIVALENT
        marker = "✓ known-equivalent" if equivalent else "❌ new survivor"
        diff = _diff_for(mutant.mutant_id)
        lines.append(
            f"<details{'' if not equivalent else ''}><summary>"
            f"{marker} — <code>{mutant.mutant_id}</code></summary>"
        )
        lines.append("")
        lines.append("```diff")
        lines.append(diff.rstrip())
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("html", "markdown"), default="html")
    args = parser.parse_args()

    if not MUTANTS_DIR.exists():
        print("No mutants/ directory — run `make mutation-test` first.", file=sys.stderr)
        return 1
    files, totals = _collect()
    if not files:
        print(
            "No *.py.meta files under mutants/ — run `make mutation-test` first.", file=sys.stderr
        )
        return 1

    if args.format == "html":
        REPORT_PATH.write_text(_render_html(files, totals))
        print(f"Wrote {REPORT_PATH}")
    else:
        sys.stdout.write(_render_markdown(files, totals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
