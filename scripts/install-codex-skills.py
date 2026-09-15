#!/usr/bin/env python3
"""Install selected skills with bundled tools; preserve or back up existing skills."""
import argparse
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path(os.environ.get(
        "CODEX_HOME", str(Path.home() / ".codex"))) / "skills")
    parser.add_argument("--skill", action="append", help="Install only this skill; repeatable")
    parser.add_argument("--replace", action="store_true", help="Back up existing skills before replacement")
    args = parser.parse_args()
    available = {p.name: p for p in (ROOT / "codex-skills").iterdir()
                 if (p / "SKILL.md").is_file()}
    names = list(dict.fromkeys(args.skill or sorted(available)))
    unknown = set(names) - available.keys()
    if unknown:
        parser.error("Unknown skills: " + ", ".join(sorted(unknown)))

    # Render canonical sources in memory so installation does not mutate checkout.
    spec = importlib.util.spec_from_file_location("sync_skills", ROOT / "scripts/sync-codex-skills.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    args.dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        target = args.dest / name
        if target.exists() or target.is_symlink():
            if not args.replace:
                print(f"Preserved existing skill: {name} (use --replace to back up and replace)")
                continue
            if target.is_symlink():
                parser.error(f"Refusing to replace symlink: {target}")
        with tempfile.TemporaryDirectory(prefix=".berkshire-stage-", dir=args.dest) as temp:
            staged = Path(temp) / name
            shutil.copytree(available[name], staged)
            source = ROOT / "skills" / (name + ".md")
            if source.is_file():
                content = source.read_text(encoding="utf-8")
                (staged / "SKILL.md").write_text(sync.metadata_for(name, source.name, content)
                                                + sync.codex_body(name, source.name, content), encoding="utf-8")
            runtime = staged / "references/runtime"
            for folder in ("tools", "skills"):
                (runtime / folder).mkdir(parents=True, exist_ok=True)
                for file in (ROOT / folder).iterdir():
                    if file.is_file() and file.suffix in (".py", ".sh", ".md"):
                        shutil.copy2(file, runtime / folder / file.name)
            shutil.copy2(ROOT / "AGENTS.md", runtime / "AGENTS.md")
            (runtime / "docs").mkdir(exist_ok=True)
            for doc in ("fork-guide.md",):
                if (ROOT / "docs" / doc).is_file():
                    shutil.copy2(ROOT / "docs" / doc, runtime / "docs" / doc)
            backup = None
            if target.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = args.dest / ".berkshire-backups" / stamp / name
                backup.parent.mkdir(parents=True)
                target.rename(backup)
            try:
                staged.rename(target)
            except OSError:
                if backup is not None:
                    backup.rename(target)
                raise
            print(f"Installed {name}" + (f"; backup: {backup}" if backup else ""))
    print(f"Skills destination: {args.dest.resolve()}; restart Codex to load changes.")


if __name__ == "__main__":
    main()
