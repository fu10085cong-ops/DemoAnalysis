import bz2
import gzip
import os
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
    assert out.name == "inner.dem"
    assert out.parent.parent == tmp_path / "cache" / "bundle.zip"
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


def test_zip_same_name_in_different_folders_not_shared(tmp_path):
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("a/map.dem", PAYLOAD)
        zf.writestr("b/map.dem", OTHER)
    first = resolve_demo(src, cache_dir=tmp_path / "cache", member="a/map.dem")
    second = resolve_demo(src, cache_dir=tmp_path / "cache", member="b/map.dem")
    assert first != second and first.name == second.name == "map.dem"
    assert first.read_bytes() == PAYLOAD
    assert second.read_bytes() == OTHER


def test_archives_with_same_stem_not_shared(tmp_path):
    with zipfile.ZipFile(tmp_path / "match.zip", "w") as zf:
        zf.writestr("match.dem", PAYLOAD)
    with gzip.open(tmp_path / "match.dem.gz", "wb") as f:
        f.write(OTHER)
    with bz2.open(tmp_path / "match.dem.bz2", "wb") as f:
        f.write(PAYLOAD + b"bz2")
    assert resolve_demo(tmp_path / "match.zip").read_bytes() == PAYLOAD
    assert resolve_demo(tmp_path / "match.dem.gz").read_bytes() == OTHER
    assert resolve_demo(tmp_path / "match.dem.bz2").read_bytes() == PAYLOAD + b"bz2"


def _bump_mtime(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))


def test_updated_gz_is_extracted_again(tmp_path):
    src = tmp_path / "match.dem.gz"
    with gzip.open(src, "wb") as f:
        f.write(PAYLOAD)
    assert resolve_demo(src, cache_dir=tmp_path / "cache").read_bytes() == PAYLOAD
    with gzip.open(src, "wb") as f:
        f.write(OTHER)
    _bump_mtime(src)
    assert resolve_demo(src, cache_dir=tmp_path / "cache").read_bytes() == OTHER


def test_updated_zip_is_extracted_again(tmp_path):
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("m.dem", PAYLOAD)
    assert resolve_demo(src, cache_dir=tmp_path / "cache").read_bytes() == PAYLOAD
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("m.dem", OTHER)
    _bump_mtime(src)
    assert resolve_demo(src, cache_dir=tmp_path / "cache").read_bytes() == OTHER


def test_unchanged_archive_reuses_extracted_file(tmp_path, monkeypatch):
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("m.dem", PAYLOAD)
    out = resolve_demo(src, cache_dir=tmp_path / "cache")
    monkeypatch.setattr(loader.shutil, "copyfileobj", lambda *a, **k: pytest.fail("不应重新解压"))
    assert resolve_demo(src, cache_dir=tmp_path / "cache") == out


def test_cache_without_source_record_is_replaced(tmp_path):
    # 旧版本留下的解压文件没有签名记录，不能直接复用
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("m.dem", PAYLOAD)
    out = resolve_demo(src, cache_dir=tmp_path / "cache")
    out.write_bytes(OTHER)
    out.with_name(out.name + ".source.json").unlink()
    assert resolve_demo(src, cache_dir=tmp_path / "cache").read_bytes() == PAYLOAD


def test_fingerprint_follows_content_not_name(tmp_path):
    a, b, c = tmp_path / "a.dem", tmp_path / "sub_b.dem", tmp_path / "c.dem"
    big = bytes(range(256)) * 20000  # 超过 2 MB，覆盖只读首尾的分支
    a.write_bytes(big)
    b.write_bytes(big)
    c.write_bytes(big[:-1] + b"x")
    assert loader.demo_fingerprint(a) == loader.demo_fingerprint(b)
    assert loader.demo_fingerprint(a) != loader.demo_fingerprint(c)


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
    assert out.name == "m1.dem" and out.parent.parent == tmp_path / "cache" / "match.rar"
    assert out.read_bytes() == PAYLOAD
    assert resolve_demo(src, cache_dir=tmp_path / "cache") == out


@needs_rar_tool
def test_updated_rar_is_extracted_again(tmp_path):
    src = write_rar5_store(tmp_path / "match.rar", {"m1.dem": PAYLOAD})
    assert resolve_demo(src, cache_dir=tmp_path / "cache").read_bytes() == PAYLOAD
    write_rar5_store(src, {"m1.dem": PAYLOAD[::-1]})  # 大小相同、内容不同
    _bump_mtime(src)
    assert resolve_demo(src, cache_dir=tmp_path / "cache").read_bytes() == PAYLOAD[::-1]


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
