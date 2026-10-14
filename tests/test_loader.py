import bz2
import gzip
import sys
import zipfile

import pytest

from cs2coach.cli import main as cli_main
from cs2coach.parsing import loader
from cs2coach.parsing.loader import DemoLoadError, find_rar_tool, resolve_demo
from tests.helpers.rar5 import write_rar5_store

PAYLOAD = b"PBDEMS2\x00fake demo payload"
OTHER = b"PBDEMS2\x00another map" * 100

needs_rar_tool = pytest.mark.skipif(find_rar_tool() is None, reason="系统中没有 unrar / 7z / bsdtar")


def test_plain_dem_returned_as_is(tmp_path):
    p = tmp_path / "match.dem"
    p.write_bytes(PAYLOAD)
    assert resolve_demo(p) == p


@pytest.mark.parametrize("suffix,opener", [(".bz2", bz2.open), (".gz", gzip.open)])
def test_stream_compressed(tmp_path, suffix, opener):
    src = tmp_path / f"match.dem{suffix}"
    with opener(src, "wb") as f:
        f.write(PAYLOAD)
    out = resolve_demo(src, cache_dir=tmp_path / "cache")
    assert out.name == "match.dem"
    assert out.read_bytes() == PAYLOAD
    # 第二次调用复用已解压文件
    assert resolve_demo(src, cache_dir=tmp_path / "cache") == out


def test_zip_extracts_single_dem_and_strips_paths(tmp_path):
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("readme.txt", "x")
        zf.writestr("../../evil/inner.dem", PAYLOAD)
    out = resolve_demo(src, cache_dir=tmp_path / "cache")
    assert out == tmp_path / "cache" / "bundle" / "inner.dem"
    assert out.read_bytes() == PAYLOAD


def test_zip_multiple_dems_require_member(tmp_path):
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("m1-mirage.dem", PAYLOAD)
        zf.writestr("sub/m2-inferno.dem", OTHER)
    with pytest.raises(DemoLoadError, match="--demo-member") as ei:
        resolve_demo(src, cache_dir=tmp_path / "cache")
    assert "m1-mirage.dem" in str(ei.value) and "sub/m2-inferno.dem" in str(ei.value)
    out = resolve_demo(src, cache_dir=tmp_path / "cache", member="m2-inferno.dem")
    assert out.read_bytes() == OTHER


def test_zip_without_dem(tmp_path):
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(DemoLoadError):
        resolve_demo(src)


def test_missing_and_unsupported(tmp_path):
    with pytest.raises(DemoLoadError):
        resolve_demo(tmp_path / "nope.dem")
    bad = tmp_path / "match.7z"
    bad.write_bytes(b"x")
    with pytest.raises(DemoLoadError):
        resolve_demo(bad)


@needs_rar_tool
def test_rar_single_dem(tmp_path):
    src = write_rar5_store(tmp_path / "match.rar", {"readme.txt": b"x", "demos/m1.dem": PAYLOAD})
    out = resolve_demo(src, cache_dir=tmp_path / "cache")
    assert out == tmp_path / "cache" / "match" / "m1.dem"
    assert out.read_bytes() == PAYLOAD
    assert resolve_demo(src, cache_dir=tmp_path / "cache") == out


@needs_rar_tool
def test_rar_multiple_dems(tmp_path):
    src = write_rar5_store(tmp_path / "bo3.rar", {"m1-mirage.dem": PAYLOAD, "m2-inferno.dem": OTHER})
    with pytest.raises(DemoLoadError, match="2 个 demo"):
        resolve_demo(src, cache_dir=tmp_path / "cache")
    assert resolve_demo(src, cache_dir=tmp_path / "cache", member="m2-inferno.dem").read_bytes() == OTHER
    assert resolve_demo(src, cache_dir=tmp_path / "cache", member="m1-mirage.dem").read_bytes() == PAYLOAD
    with pytest.raises(DemoLoadError, match="未找到"):
        resolve_demo(src, cache_dir=tmp_path / "cache", member="m3.dem")


@needs_rar_tool
def test_rar_same_member_name_in_different_archives_not_shared(tmp_path):
    a = write_rar5_store(tmp_path / "a.rar", {"game.dem": PAYLOAD})
    b = write_rar5_store(tmp_path / "b.rar", {"game.dem": OTHER})
    assert resolve_demo(a, cache_dir=tmp_path / "c").read_bytes() == PAYLOAD
    assert resolve_demo(b, cache_dir=tmp_path / "c").read_bytes() == OTHER


def test_rar_without_tool_gives_install_hint(tmp_path, monkeypatch):
    src = write_rar5_store(tmp_path / "match.rar", {"m1.dem": PAYLOAD})
    monkeypatch.setattr(loader, "find_rar_tool", lambda: None)
    with pytest.raises(DemoLoadError, match="libarchive-tools"):
        resolve_demo(src, cache_dir=tmp_path / "cache")


def test_rar_tool_short_output_is_rejected(tmp_path, monkeypatch):
    src = write_rar5_store(tmp_path / "match.rar", {"m1.dem": PAYLOAD})
    fake = [sys.executable, "-c", "import sys; sys.stdout.write('x')"]
    monkeypatch.setattr(loader, "_rar_extract_cmd", lambda archive, member: fake)
    with pytest.raises(DemoLoadError, match="期望"):
        resolve_demo(src, cache_dir=tmp_path / "cache")
    assert not list((tmp_path / "cache").rglob("*.dem*"))


def test_rar_not_an_archive(tmp_path):
    src = tmp_path / "broken.rar"
    src.write_bytes(b"not a rar")
    with pytest.raises(DemoLoadError, match="无法读取"):
        resolve_demo(src)


def test_cli_reports_load_error(tmp_path, capsys):
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("a.dem", PAYLOAD)
        zf.writestr("b.dem", OTHER)
    assert cli_main(["analyze", str(src), "--player", "1"]) == 1
    assert "--demo-member" in capsys.readouterr().err
