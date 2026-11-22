import json
import zipfile

import pytest

from scripts.stamp_version import parse_tag, stamp
from scripts.verify_package import REQUIRED_FILES, verify


def _write_package(tmp_path, version="1.2.3"):
    path = tmp_path / "package.json"
    path.write_text(json.dumps({"name": "decky-led-lights", "version": version}))
    return path


def test_parse_tag_accepts_release_tags():
    assert parse_tag("v1.2.3") == "1.2.3"
    assert parse_tag("1.2.3") == "1.2.3"
    assert parse_tag("v1.2.3-rc.1") == "1.2.3-rc.1"


def test_parse_tag_rejects_other_refs():
    with pytest.raises(SystemExit):
        parse_tag("main")
    with pytest.raises(SystemExit):
        parse_tag("v1.2")


def test_stamp_writes_the_version(tmp_path):
    path = _write_package(tmp_path, "1.0.0")
    assert stamp("1.1.2", path) == "1.1.2"
    assert json.loads(path.read_text())["version"] == "1.1.2"


def _archive(tmp_path, version="1.2.3", skip=None, bundle_size=60_000, name=None):
    path = tmp_path / (name or f"LedLights-{version}.zip")
    plugin = {"name": "Led Lights", "flags": ["root", "_root"], "api_version": 1}
    package = {"name": "decky-led-lights", "version": version}
    with zipfile.ZipFile(path, "w") as zf:
        for required in REQUIRED_FILES:
            if required == skip:
                continue
            if required == "plugin.json":
                zf.writestr("Led Lights/plugin.json", json.dumps(plugin))
            elif required == "package.json":
                zf.writestr("Led Lights/package.json", json.dumps(package))
            elif required == "dist/index.js":
                zf.writestr("Led Lights/dist/index.js", "x" * bundle_size)
            else:
                zf.writestr(f"Led Lights/{required}", "content")
    return path


def test_a_complete_archive_passes(tmp_path):
    assert verify(_archive(tmp_path)) == []


def test_a_missing_bundle_fails(tmp_path):
    problems = verify(_archive(tmp_path, skip="dist/index.js"))
    assert any("dist/index.js" in problem for problem in problems)


def test_a_tiny_bundle_fails(tmp_path):
    problems = verify(_archive(tmp_path, bundle_size=10))
    assert any("suspiciously small" in problem for problem in problems)


def test_the_archive_name_must_match_the_version(tmp_path):
    problems = verify(_archive(tmp_path, name="LedLights-9.9.9.zip"))
    assert any("does not match version" in problem for problem in problems)


def test_two_top_level_folders_fail(tmp_path):
    path = _archive(tmp_path)
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("Other/plugin.json", "{}")
    problems = verify(path)
    assert any("one top-level folder" in problem for problem in problems)
