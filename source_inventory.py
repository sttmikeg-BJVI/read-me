"""Source integrity inventory and migration receipt.

A migration is only trustworthy if you can show that what arrived is what
left. This hashes a source tree into an inventory, and compares two
inventories to produce a receipt that names every file that was added,
removed or changed.

It is the tool for moving the Floot export into git: inventory the export,
inventory the repository after the push, compare, and keep the receipt. The
comparison is content-based, so a file that was silently regenerated instead
of moved shows up as `changed`.

    python source_inventory.py scan /path/to/export -o export.json
    python source_inventory.py scan /path/to/repo   -o repo.json
    python source_inventory.py compare export.json repo.json

Exit code is 1 when the comparison is not clean, so it can gate a migration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan(root: Path, skip_dirs: set[str] = SKIP_DIRS) -> dict:
    root = root.resolve()
    files: dict[str, dict] = {}
    for path in sorted(root.rglob("*")):
        if any(part in skip_dirs for part in path.relative_to(root).parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        relative = str(path.relative_to(root))
        files[relative] = {"sha256": file_digest(path), "bytes": path.stat().st_size}
    return {
        "root": str(root),
        "file_count": len(files),
        "total_bytes": sum(entry["bytes"] for entry in files.values()),
        "tree_digest": hashlib.sha256(
            "\n".join(f"{name} {entry['sha256']}" for name, entry in files.items()).encode()
        ).hexdigest(),
        "files": files,
    }


@dataclass(frozen=True)
class Comparison:
    missing: tuple[str, ...]
    added: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.missing or self.changed)


def compare(source: dict, destination: dict) -> Comparison:
    src, dst = source["files"], destination["files"]
    return Comparison(
        missing=tuple(sorted(set(src) - set(dst))),
        added=tuple(sorted(set(dst) - set(src))),
        changed=tuple(
            sorted(
                name
                for name in set(src) & set(dst)
                if src[name]["sha256"] != dst[name]["sha256"]
            )
        ),
    )


def receipt(source: dict, destination: dict, result: Comparison) -> str:
    lines = [
        "# Source integrity receipt",
        "",
        f"- source: `{source['root']}` — {source['file_count']} files, "
        f"tree digest `{source['tree_digest'][:16]}`",
        f"- destination: `{destination['root']}` — {destination['file_count']} files, "
        f"tree digest `{destination['tree_digest'][:16]}`",
        f"- verdict: **{'INTACT' if result.clean else 'NOT INTACT'}**",
        "",
    ]
    for title, names in (
        ("Missing from destination", result.missing),
        ("Changed content", result.changed),
        ("Added in destination", result.added),
    ):
        lines.append(f"## {title} ({len(names)})")
        lines.extend(f"- `{name}`" for name in names[:200])
        if len(names) > 200:
            lines.append(f"- … and {len(names) - 200} more")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scan_cmd = sub.add_parser("scan")
    scan_cmd.add_argument("root")
    scan_cmd.add_argument("-o", "--output")

    compare_cmd = sub.add_parser("compare")
    compare_cmd.add_argument("source")
    compare_cmd.add_argument("destination")
    compare_cmd.add_argument("-o", "--output")

    args = parser.parse_args(argv)

    if args.command == "scan":
        inventory = scan(Path(args.root))
        text = json.dumps(inventory, indent=2, sort_keys=True)
        if args.output:
            Path(args.output).write_text(text + "\n")
            print(f"{inventory['file_count']} files, tree digest {inventory['tree_digest']}")
        else:
            print(text)
        return 0

    source = json.loads(Path(args.source).read_text())
    destination = json.loads(Path(args.destination).read_text())
    result = compare(source, destination)
    text = receipt(source, destination, result)
    if args.output:
        Path(args.output).write_text(text + "\n")
    print(text)
    return 0 if result.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
