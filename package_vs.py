"""Build the portable VS application archive."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


APP_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = "VS"
APP_FILES = (
    ".gitignore",
    "README.md",
    "app.py",
    "image_engine.py",
    "module1.py",
    "module2.py",
    "package_vs.py",
    "portable_paths.py",
    "run.bat",
    "run.sh",
    "storage.py",
    "timeline_engine.py",
    "tts_engine.py",
    "video_renderer.py",
)
APP_DIRECTORIES = ("image_providers", "static", "tts_providers")


def application_files() -> list[Path]:
    files = [APP_ROOT / relative for relative in APP_FILES]
    for directory in APP_DIRECTORIES:
        files.extend(
            path
            for path in (APP_ROOT / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    return sorted(files, key=lambda path: path.relative_to(APP_ROOT).as_posix())


def build_package(output: str | Path | None = None) -> Path:
    destination = Path(output) if output else APP_ROOT / "VS.zip"
    if not destination.is_absolute():
        destination = APP_ROOT / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for source in application_files():
                relative = source.relative_to(APP_ROOT).as_posix()
                archive.write(source, f"{PACKAGE_ROOT}/{relative}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Create VS.zip with a VS/ root directory")
    parser.add_argument("--output", help="output ZIP path; defaults to VS.zip beside this script")
    args = parser.parse_args()
    output = build_package(args.output)
    print(f"Created {output}")


if __name__ == "__main__":
    main()
