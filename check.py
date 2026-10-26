#!/usr/bin/env python3
"""check.py — sanity-check built CV PDFs before you send them out.

Four things go wrong with CV PDFs, and all four are invisible when you just
look at the page:

1. It spilled onto a second page (half-empty page two).
2. A placeholder like [X] was never filled in.
3. The text extracts in the wrong order, so an ATS mangles your history.
4. The links look right but were never embedded as links.

This script catches all four. Exit code is 1 if anything failed, so it can run
in CI.

Usage:
    python check.py dist
    python check.py dist --pages 1 --strict
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent

PLACEHOLDER = re.compile(r"\[[^\]\n]{1,30}\]")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

REQUIRED_SECTIONS = ("EXPERIENCE", "EDUCATION", "SKILLS")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def headings(text: str, max_len: int = 45) -> list[str]:
    """Return likely section headings: short, fully-uppercase lines.

    Matching raw substrings is not enough — the word "experience" also appears in
    prose ("enterprise experience across..."), which silently breaks any
    position-based order check.
    """
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > max_len:
            continue
        letters = [c for c in line if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            found.append(line.upper())
    return found


def collect(pdf_path: Path) -> tuple[list[str], list[str], int]:
    """Return (page texts, embedded link URIs, page count)."""
    reader = PdfReader(str(pdf_path))
    texts = [(page.extract_text() or "") for page in reader.pages]

    uris: list[str] = []
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            action = annot.get_object().get("/A")
            if action and action.get("/URI"):
                uris.append(str(action["/URI"]))
    return texts, uris, len(reader.pages)


def load_expected(data_path: Path | None) -> dict:
    if data_path and data_path.exists():
        return json.loads(data_path.read_text(encoding="utf-8"))
    return {}


def check_pdf(pdf_path: Path, expected: dict, max_pages: int) -> list[tuple[str, str, str]]:
    """Return a list of (status, check name, detail)."""
    texts, uris, page_count = collect(pdf_path)
    full = normalise("\n".join(texts))
    results: list[tuple[str, str, str]] = []

    # 1. page count -----------------------------------------------------------
    if page_count <= max_pages:
        results.append((PASS, "page count", f"{page_count} page(s)"))
    else:
        results.append(
            (FAIL, "page count", f"{page_count} pages, expected {max_pages} — tighten spacing or cut a line")
        )

    # 2. placeholders ---------------------------------------------------------
    leftovers = sorted(set(PLACEHOLDER.findall(full)))
    if leftovers:
        results.append((WARN, "placeholders", f"unfilled: {', '.join(leftovers)}"))
    else:
        results.append((PASS, "placeholders", "none found"))

    # 3. sections present -----------------------------------------------------
    found = headings("\n".join(texts))

    def heading_index(keyword: str) -> int:
        for i, line in enumerate(found):
            if keyword in line:
                return i
        return -1

    positions = {name: heading_index(name) for name in REQUIRED_SECTIONS}
    missing = [name for name, pos in positions.items() if pos < 0]
    if missing:
        results.append((FAIL, "sections", f"no heading found for: {', '.join(missing).lower()}"))
    else:
        results.append((PASS, "sections", "experience / education / skills all present"))

    # 4. section order --------------------------------------------------------
    if not missing:
        order = sorted(positions, key=positions.get)
        if order == ["SKILLS", "EXPERIENCE", "EDUCATION"]:
            results.append((PASS, "section order", "skills > experience > education"))
        else:
            results.append(
                (
                    WARN,
                    "section order",
                    f"reads as {' > '.join(order).lower()} — a side column usually causes this",
                )
            )

    # 5. ATS readability ------------------------------------------------------
    name = expected.get("meta", {}).get("name")
    if name:
        head = normalise(full[:120]).lower()
        if head.startswith(name.lower()):
            results.append((PASS, "ATS readability", "name is the first text on the page"))
        else:
            results.append(
                (
                    WARN,
                    "ATS readability",
                    "name is not contiguous at the top — a multi-column layout usually causes this",
                )
            )

    # 6. contact details ------------------------------------------------------
    email = expected.get("meta", {}).get("email")
    if email:
        if email.lower() in full.lower():
            results.append((PASS, "email", email))
        else:
            results.append((FAIL, "email", f"{email} not found in extracted text"))

    # 7. links ----------------------------------------------------------------
    if uris:
        bad = [u for u in uris if not u.startswith(("http://", "https://"))]
        if bad:
            results.append((WARN, "links", f"non-http link(s): {', '.join(bad)}"))
        else:
            results.append((PASS, "links", f"{len(uris)} clickable link(s)"))
    else:
        results.append((WARN, "links", "no clickable links embedded"))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify built CV PDFs.")
    parser.add_argument("target", type=Path, nargs="?", default=ROOT / "dist", help="PDF file or directory")
    parser.add_argument("--data", type=Path, default=None, help="resume JSON used for expectations")
    parser.add_argument("--pages", type=int, default=1, help="maximum allowed pages (default: 1)")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args()

    target = args.target
    pdfs = sorted(target.glob("*.pdf")) if target.is_dir() else [target]
    if not pdfs:
        sys.exit(f"No PDFs found in {target} — run build.py first.")

    data_path = args.data or ROOT / "data" / "resume.json"
    expected = load_expected(data_path if data_path.exists() else None)

    failed = warned = 0
    for pdf in pdfs:
        print(f"\n{pdf.name}")
        for status, name, detail in check_pdf(pdf, expected, args.pages):
            print(f"  {status:<4}  {name:<16}  {detail}")
            failed += status == FAIL
            warned += status == WARN

    print(f"\n{len(pdfs)} file(s) checked — {failed} failed, {warned} warning(s)")
    if failed or (args.strict and warned):
        sys.exit(1)


if __name__ == "__main__":
    main()
