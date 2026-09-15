#!/usr/bin/env python3
"""Generate Codex skills from AI Berkshire Claude command files."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_SKILLS = ROOT / "skills"
CODEX_SKILLS = ROOT / "codex-skills"


def split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 5 :].lstrip("\n")


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def yaml_quote(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def metadata_for(name: str, source_name: str, source_text: str) -> str:
    existing, body = split_frontmatter(source_text)
    if existing:
        has_name = re.search(r"(?m)^name:\s*", existing) is not None
        has_description = re.search(r"(?m)^description:\s*", existing) is not None
        lines = []
        if not has_name:
            lines.append(f"name: {name}")
        if not has_description:
            title = first_heading(body, name)
            lines.append(
                "description: "
                + yaml_quote(f"AI Berkshire skill: {title}. Source: skills/{source_name}.")
            )
        lines.append(existing.rstrip())
        return "---\n" + "\n".join(lines) + "\n---\n\n"

    title = first_heading(source_text, name)
    description = f"AI Berkshire skill: {title}. Source: skills/{source_name}."
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {yaml_quote(description)}\n"
        "---\n\n"
    )


def codex_body(name: str, source_name: str, source_text: str) -> str:
    _, body = split_frontmatter(source_text)
    note = f"""## Codex runtime rules (override Claude-specific mechanics below)

Generated from `skills/{source_name}`. Preserve its research questions; apply
these rules instead of conflicting runtime, permission, output or sizing steps.

- Treat `$ARGUMENTS` as the current user request. An explicit request to run this
  research already authorizes its ordinary research steps: show the plan and
  proceed; do not require a second confirmation of team structure.
- **Web preflight:** perform one actual read-only search/fetch with an available
  Codex tool and inspect the result before delegating. Do not read `.claude/`
  settings, require a `WebSearch` whitelist, or change permissions in Codex.
  On failure report the exact limitation; use provided primary documents or
  return a source-gap report. Never claim fresh data from training knowledge.
- **Team mapping:** use available subagent/message/wait capabilities, not literal
  `TeamCreate`, `TaskCreate`, `TaskUpdate`, `TeamDelete` or `shutdown_request`.
  Respect the actual capacity INCLUDING the lead. Run four research roles in
  batches when four children cannot run together; if no subagents are available,
  perform four clearly separated role analyses sequentially and disclose that.
  Do not claim independent agents when a single agent did the work.
- **Tool base:** resolve paths relative to the loaded SKILL.md. For installed
  packages, use `references/runtime/` as the base for `tools/`, `skills/`,
  `AGENTS.md` and `docs/fork-guide.md`. For a checkout, use the repository root.
  Use absolute paths or set command cwd to that base. Do not assume
  `~/ai-berkshire` exists. Read the fork guide and AGENTS.md before research.
  The bundle supports core calculation/audit; data-dependent auxiliary tools
  may still require the full checkout and external services.
- **Workspace:** resolve the user's project directory before running tools;
  save new personal reports and raw evidence to that project's ignored
  `local/research/<company>/<date>/`, not the skill installation directory or
  a hard-coded home-directory report path.
  When tool cwd is the runtime base, pass absolute project/report/output paths
  so relative report arguments cannot resolve inside the installation.
- Confirm the actual current date/time and label data cutoff, price timestamp,
  market timezone, currency, period and accounting basis. Default to Chinese.
- **Futu:** discover callable Futu tools or read the installed futuapi skill.
  Use supported read-only quotes/news/financials when available; reconcile key
  fundamentals to original filings. Preserve provenance and timestamps.
  If unavailable, disclose it and use original filings plus an available second
  source. Research never authorizes trading or account mutations.
- **Evidence:** two copied articles are not independent evidence. Record source
  URLs/file paths, page/API locators and shared upstream provenance.
  Separate numeric verification channels (e.g. filing vs Futu extraction) can
  catch transcription errors even with one original filing; disclose that they
  are not independent factual evidence for a business claim.
  Validate all decision-critical inputs, with random sampling only as a supplement.
  Audit FAIL or INCOMPLETE blocks verified/publication-ready claims. PASS only
  means supplied sample values match, not that the full report is true.
- **Recommendations:** distinguish observed facts, assumptions and judgments.
  State scenario assumptions and evidence gaps. Without holdings, investment
  horizon and risk limits, do not invent personalized position percentages;
  give watch conditions or explicitly hypothetical examples. Do not force a
  target price when current data or valuation assumptions are unsupported.

"""
    return note + body.rstrip() + "\n"


def main() -> None:
    check = "--check" in sys.argv[1:]
    unknown_args = [arg for arg in sys.argv[1:] if arg != "--check"]
    if unknown_args:
        joined = ", ".join(unknown_args)
        raise SystemExit(f"Unknown argument(s): {joined}")

    if not check:
        CODEX_SKILLS.mkdir(exist_ok=True)

    count = 0
    stale: list[str] = []
    for source in sorted(CLAUDE_SKILLS.glob("*.md")):
        name = source.stem
        source_text = source.read_text(encoding="utf-8")
        target_dir = CODEX_SKILLS / name
        target = target_dir / "SKILL.md"
        content = metadata_for(name, source.name, source_text) + codex_body(
            name, source.name, source_text
        )
        if check:
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        count += 1

    if check:
        if stale:
            print("Codex skills are out of date:")
            for path in stale:
                print(f"  {path}")
            raise SystemExit(1)
        print(f"Checked {count} Codex skills in {CODEX_SKILLS.relative_to(ROOT)}")
        return

    print(f"Generated {count} Codex skills in {CODEX_SKILLS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
