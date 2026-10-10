"""Builds `app/help/docs.json` from the user guide (`website/docs`, KO, and its EN translation).

The API image carries only `apps/api`, so the guide the chat reads when a question is about
KloudChat itself (`services/app_help.py`) is kept here as sections: one per `##` heading, with
its page title and path. Run after editing the guide:

    python apps/api/scripts/build_help.py

`tests/test_app_help.py` fails when the file is out of date with the guide.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API = Path(__file__).resolve().parents[1]
SOURCES = {
    "ko": ROOT / "website" / "docs",
    "en": ROOT / "website" / "i18n" / "en" / "docusaurus-plugin-content-docs" / "current",
}
OUT = API / "app" / "help" / "docs.json"
_MAX = 1800

_FRONT = re.compile(r"\A---\n.*?\n---\n", re.S)
_TITLE = re.compile(r"^title:\s*(.+)$", re.M)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_TAG = re.compile(r"</?[A-Za-z][^>\n]{0,200}>")
_ADMONITION = re.compile(r"^:::\s{0,2}\w*.*$", re.M)
_IMPORT = re.compile(r"^(?:import|export)\s.*$", re.M)


def _clean(text: str) -> str:
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _TAG.sub("", text)
    text = _ADMONITION.sub("", text)
    text = _IMPORT.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def sections() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for lang, root in SOURCES.items():
        for path in sorted(root.rglob("*.md")):
            raw = path.read_text(encoding="utf-8")
            front = _FRONT.match(raw)
            title = ""
            if front:
                found = _TITLE.search(front.group(0))
                title = found.group(1).strip().strip("'\"") if found else ""
                raw = raw[front.end():]
            rel = str(path.relative_to(root).with_suffix(""))
            parts = re.split(r"^##\s+", raw, flags=re.M)
            head = _clean(re.sub(r"^#\s+.*$", "", parts[0], count=1, flags=re.M))
            if head:
                out.append({"lang": lang, "path": rel, "title": title, "heading": "",
                            "text": head[:_MAX]})
            for part in parts[1:]:
                heading, _, body = part.partition("\n")
                body = _clean(body)
                if body:
                    out.append({"lang": lang, "path": rel, "title": title,
                                "heading": heading.strip(), "text": body[:_MAX]})
    return out


def main() -> None:
    OUT.write_text(json.dumps(sections(), ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(sections())} sections", file=sys.stderr)


if __name__ == "__main__":
    main()
