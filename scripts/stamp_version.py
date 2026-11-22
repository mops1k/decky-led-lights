"""Stamp the plugin version from a release tag.

Release tags are ``vX.Y.Z``; the version that ends up in package.json (and therefore
in the ZIP name and the plugin's reported version) comes from the tag, so a release
can never ship a stale number.

Usage: python3 scripts/stamp_version.py v1.2.3
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def parse_tag(tag: str) -> str:
    match = SEMVER.match((tag or "").strip())
    if not match:
        raise SystemExit(f"not a semver tag: {tag!r}")
    version = ".".join(match.group(1, 2, 3))
    if match.group(4):
        version += f"-{match.group(4)}"
    return version


def stamp(version: str, path=None) -> str:
    package = Path(path or ROOT / "package.json")
    data = json.loads(package.read_text(encoding="utf-8"))
    data["version"] = version
    package.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return version


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: stamp_version.py <tag>\n")
        return 2
    version = stamp(parse_tag(argv[1]))
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
