"""GSAP is fetched from npm on first launch, never shipped (vendor.py)."""

from __future__ import annotations

import io
import subprocess
import tarfile

import pytest

from synthlab.studio import vendor


def test_gsap_is_not_tracked_and_is_ignored():
    tracked = subprocess.run(
        ["git", "ls-files", "synthlab/studio/static/gsap.min.js"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert tracked == ""
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "synthlab/studio/static/gsap.min.js"]
    )
    assert ignored.returncode == 0


def test_the_pin_is_one_exact_npm_version():
    assert vendor.GSAP.tarball == "https://registry.npmjs.org/gsap/-/gsap-3.13.0.tgz"
    assert vendor.GSAP.integrity.startswith("sha512-")
    assert len(vendor.GSAP.sha256) == 64


def test_a_tarball_that_is_not_the_pinned_one_is_refused():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"not gsap"
        info = tarfile.TarInfo(vendor.GSAP.member)
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="integrity"):
        vendor._verified(vendor.GSAP, buf.getvalue())


def test_ensure_skips_a_file_already_there(tmp_path, monkeypatch):
    (tmp_path / "gsap.min.js").write_text("// already fetched", encoding="utf-8")
    calls = []
    monkeypatch.setattr(vendor, "fetch", lambda *a: calls.append(a))
    vendor.ensure(tmp_path, out=io.StringIO())
    assert calls == []


def test_offline_the_studio_starts_without_it(tmp_path):
    out = io.StringIO()
    vendor.ensure(tmp_path, out=out)  # the suite's guard refuses the download
    assert not (tmp_path / "gsap.min.js").exists()
    assert not list(tmp_path.iterdir())  # no half-written file
    assert "runs without its motion" in out.getvalue()
