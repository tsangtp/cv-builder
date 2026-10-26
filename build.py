#!/usr/bin/env python3
"""cv-builder — one JSON in, two print-ready PDFs out.

Renders a resume JSON through a Jinja2 template, then prints the HTML to PDF
with a headless Chromium browser (Chrome or Edge).

No LaTeX, no GTK, no native PDF libraries — if you can open Chrome you can
build this. That is the whole reason the project exists.

Usage:
    python build.py                     # build both templates from data/resume.json
    python build.py --template ats      # build one
    python build.py --data data/x.json --out dist
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
STYLE_DIR = ROOT / "styles"

# template name -> (jinja template file, css file)
TEMPLATES = {
    "modern": ("modern.html", "modern.css"),
    "ats": ("ats.html", "ats.css"),
}

REQUIRED_KEYS = ("meta", "summary", "skills", "experience", "education")

CHROME_ENV = "CHROME_PATH"


# --------------------------------------------------------------------------- #
# browser discovery
# --------------------------------------------------------------------------- #
def find_chrome() -> str:
    """Return a path to a Chromium-based browser, or exit with a helpful error."""
    override = os.environ.get(CHROME_ENV)
    if override:
        if Path(override).exists():
            return override
        sys.exit(f"{CHROME_ENV} is set to {override!r} but that file does not exist.")

    candidates: list[Path] = []
    system = platform.system()

    if system == "Windows":
        for base in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if not base:
                continue
            candidates += [
                Path(base) / "Google/Chrome/Application/chrome.exe",
                Path(base) / "Microsoft/Edge/Application/msedge.exe",
            ]
    elif system == "Darwin":
        candidates += [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]

    for path in candidates:
        if path.exists():
            return str(path)

    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge"):
        found = shutil.which(name)
        if found:
            return found

    sys.exit(
        "Could not find Chrome or Edge.\n"
        f"Install one, or point {CHROME_ENV} at the executable:\n"
        f"  export {CHROME_ENV}=/path/to/chrome"
    )


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def load_data(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"Data file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"{path} is not valid JSON: {exc}")

    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        sys.exit(f"{path} is missing required key(s): {', '.join(missing)}")
    return data


def render(template_name: str, data: dict) -> str:
    """Render one template with its CSS inlined, so the HTML is self-contained."""
    template_file, css_file = TEMPLATES[template_name]
    css = (STYLE_DIR / css_file).read_text(encoding="utf-8")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        undefined=ChainableUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template(template_file).render(css=css, **data)


def print_to_pdf(chrome: str, html: str, out_path: Path) -> None:
    """Write `html` to a temp file and ask Chromium to print it to `out_path`."""
    # Chromium resolves relative paths against its own cwd, so hand it an absolute one.
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "resume.html"
        html_path.write_text(html, encoding="utf-8")

        cmd = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            f"--print-to-pdf={out_path}",
            html_path.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

    # Chromium can exit 0 without writing anything, so verify the file itself.
    if not out_path.exists() or out_path.stat().st_size < 1000:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        sys.exit(f"Chromium did not produce {out_path.name}.\n{detail}")


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def default_data_path() -> Path:
    """Prefer your real resume; fall back to the shipped example."""
    real = ROOT / "data" / "resume.json"
    return real if real.exists() else ROOT / "data" / "example.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build print-ready CV PDFs from JSON.")
    parser.add_argument(
        "--template",
        default="all",
        choices=[*TEMPLATES, "all"],
        help="which layout to build (default: all)",
    )
    parser.add_argument("--data", type=Path, default=None, help="resume JSON (default: data/resume.json)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output directory (default: dist/)")
    args = parser.parse_args()

    data_path = args.data or default_data_path()
    data = load_data(data_path)
    chrome = find_chrome()
    slug = slugify(data["meta"].get("name", "resume")) or "resume"

    selected = list(TEMPLATES) if args.template == "all" else [args.template]
    print(f"data:     {data_path.relative_to(ROOT) if data_path.is_relative_to(ROOT) else data_path}")
    print(f"browser:  {chrome}")

    for name in selected:
        out_path = args.out / f"{slug}_cv_{name}.pdf"
        print_to_pdf(chrome, render(name, data), out_path)
        size_kb = out_path.stat().st_size / 1024
        print(f"built:    {out_path}  ({size_kb:.0f} KB)")

    print(f"\nDone. Run `python check.py {args.out}` to verify the output.")


if __name__ == "__main__":
    main()
