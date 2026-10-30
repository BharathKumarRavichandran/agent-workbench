#!/usr/bin/env python3
"""Render native marketplace manifests from catalog/plugins.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalog" / "plugins.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def plugin_description(package: Path, agent: str) -> str:
    manifest = package / f".{agent}-plugin" / "plugin.json"
    if not manifest.is_file():
        manifest = package / "plugin.json"
    data = load_json(manifest)
    return data["description"]


def rendered_documents() -> dict[Path, dict]:
    catalog = load_json(CATALOG_PATH)
    marketplace = catalog["marketplace"]
    documents: dict[Path, dict] = {}

    for agent in ("cursor", "claude"):
        plugins = []
        for plugin in catalog["plugins"]:
            source = plugin["packages"][agent]
            package = ROOT / source
            plugins.append(
                {
                    "name": plugin["id"],
                    "source": f"./{source}" if agent == "claude" else source,
                    "description": plugin_description(package, agent),
                }
            )

        document = {
            "name": marketplace["name"],
            "owner": marketplace["owner"],
            "metadata": {"description": marketplace["description"]},
            "plugins": plugins,
        }
        if agent == "claude":
            document.pop("metadata")
        documents[ROOT / f".{agent}-plugin" / "marketplace.json"] = document

    return documents


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files differ")
    args = parser.parse_args()

    stale = []
    for path, document in rendered_documents().items():
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")

    if stale:
        print("stale generated files:")
        for path in stale:
            print(f"  {path}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
