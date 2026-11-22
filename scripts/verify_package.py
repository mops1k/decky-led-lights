"""Verify that a packaged plugin ZIP is installable.

Catches the boring failures before a release goes out: a missing bundle, a Python
module dropped from the archive list, or an archive without the single top-level
folder Decky expects.

Usage: python3 scripts/verify_package.py [path/to/plugin.zip]
"""
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
MIN_BUNDLE_BYTES = 50_000
REQUIRED_FILES = (
    "main.py",
    "plugin.json",
    "package.json",
    "LICENSE",
    "NOTICE",
    "README.md",
    "dist/index.js",
    "py_modules/pw_capture.py",
    "py_modules/kwin_capture.py",
    "py_modules/ambilight.py",
    "py_modules/ambient_sampling.py",
    "py_modules/audio.py",
)


def find_archive(argument=None) -> Path:
    if argument:
        return Path(argument)
    archives = sorted(PACKAGES.glob("*.zip"))
    if not archives:
        raise SystemExit("no ZIP in packages/ - run scripts/package.py first")
    return archives[-1]


def verify(path: Path) -> list:
    problems = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        roots = {name.split("/")[0] for name in names}
        if len(roots) != 1:
            problems.append(f"archive must have one top-level folder, got {sorted(roots)}")
            return problems
        root = roots.pop()
        names_set = set(names)

        for required in REQUIRED_FILES:
            if f"{root}/{required}" not in names_set:
                problems.append(f"missing {required}")

        if f"{root}/plugin.json" in names_set:
            plugin = json.loads(archive.read(f"{root}/plugin.json"))
            if not plugin.get("name"):
                problems.append("plugin.json has no name")
            if "root" not in (plugin.get("flags") or []):
                problems.append("plugin.json must declare the root flag")
            if plugin.get("api_version") != 1:
                problems.append("plugin.json must use api_version 1")

        if f"{root}/package.json" in names_set:
            package = json.loads(archive.read(f"{root}/package.json"))
            version = str(package.get("version", ""))
            if not SEMVER.match(version):
                problems.append(f"package.json version is not semver: {version!r}")
            if path.stem != f"LedLights-{version}":
                problems.append(
                    f"archive name {path.name} does not match version {version}"
                )

        if f"{root}/dist/index.js" in names_set:
            size = len(archive.read(f"{root}/dist/index.js"))
            if size < MIN_BUNDLE_BYTES:
                problems.append(f"dist/index.js is suspiciously small ({size} bytes)")
    return problems


def main(argv):
    if len(argv) > 2:
        sys.stderr.write("usage: verify_package.py [plugin.zip]\n")
        return 2
    path = find_archive(argv[1] if len(argv) == 2 else None)
    if not path.is_file():
        raise SystemExit(f"no such ZIP: {path}")
    problems = verify(path)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(f"OK: {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
