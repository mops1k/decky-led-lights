"""Package the built plugin into dist/ independent Decky ZIP.

The plugin name comes from plugin.json, so the archive carries one top-level
folder with that name, which is what Decky's "Install Plugin from ZIP" expects.

Usage: python3 scripts/package.py
"""
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"
INCLUDE_DIRS = ("dist", "py_modules", "assets")
INCLUDE_FILES = (
    "main.py",
    "plugin.json",
    "package.json",
    "README.md",
    "README.ru.md",
    "LICENSE",
    "NOTICE",
)
SKIP_PARTS = {"__pycache__", ".pytest_cache"}


def plugin_name() -> str:
    return json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["name"]


def version() -> str:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


def copy_plugin(staging: Path) -> Path:
    target = staging / plugin_name()
    target.mkdir(parents=True)
    for directory in INCLUDE_DIRS:
        source = ROOT / directory
        if not source.is_dir():
            raise SystemExit(f"missing {directory}/ - run 'pnpm build' first")
        shutil.copytree(
            source,
            target / directory,
            ignore=shutil.ignore_patterns(*SKIP_PARTS, "*.map"),
        )
    for filename in INCLUDE_FILES:
        source = ROOT / filename
        if source.exists():
            shutil.copy2(source, target / filename)
    return target


def build_zip() -> Path:
    PACKAGES.mkdir(exist_ok=True)
    archive = PACKAGES / f"LedLights-{version()}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        copy_plugin(staging)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                zf.write(path, path.relative_to(staging))
    return archive


if __name__ == "__main__":
    path = build_zip()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    print(f"{path} ({path.stat().st_size} bytes, {len(names)} entries)")
    for name in names[:12]:
        print(" ", name)
