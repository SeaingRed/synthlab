"""Build the native DSP extension's wheels (crates/ -> crates/wheels/) for
both architectures (x86-64 and ARM64), so only the machine that edits the
Rust needs a toolchain.

    uv run python tools/build_wheels.py           # build both, stamp, reinstall
    uv run python tools/build_wheels.py --check   # committed wheels == sources?

Every machine installs the committed wheel for its arch (`[tool.uv.sources]`
in pyproject.toml): win_amd64 on x86-64, win_arm64 on Windows on ARM.
The ARM64 one is cross-compiled here (rustup target
aarch64-pc-windows-msvc + the VS "MSVC ARM64 build tools"); abi3-py313, so
one wheel serves 3.13 and 3.14.

Each build is stamped with a hash of the Rust sources (`source_hash`,
line endings normalised so a CRLF checkout hashes the same), recorded in
crates/wheels/SOURCE and compiled into the module (`build_info()["source"]`).
tests/test_native.py fails when either no longer matches the sources: the
Rust changed and the wheels weren't rebuilt.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATES = ROOT / "crates"
WHEELS = CRATES / "wheels"
STAMP = WHEELS / "SOURCE"
MATURIN = "maturin==1.15.0"
TARGETS = ("x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc")


def sources() -> list[Path]:
    """Everything that decides what the extension computes."""
    files = [
        CRATES / "Cargo.toml",
        CRATES / "Cargo.lock",
        CRATES / ".cargo" / "config.toml",
        CRATES / "dsp" / "Cargo.toml",
        CRATES / "dsp-py" / "Cargo.toml",
        CRATES / "dsp-py" / "pyproject.toml",
    ]
    for crate in ("dsp", "dsp-py"):
        files += (CRATES / crate / "src").rglob("*.rs")
    return sorted(files)


def source_hash() -> str:
    h = hashlib.sha256()
    for f in sources():
        h.update(f.relative_to(CRATES).as_posix().encode() + b"\0")
        h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


def recorded() -> str:
    return STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""


def build(stamp: str) -> None:
    cargo_bin = Path.home() / ".cargo" / "bin"
    env = {
        **os.environ,
        "SYNTHLAB_DSP_SOURCE": stamp,
        "PATH": f"{cargo_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    if shutil.which("cargo", path=env["PATH"]) is None:
        sys.exit("cargo isn't on PATH: install rustup (crates/README.md) first")
    with tempfile.TemporaryDirectory() as tmp:
        for target in TARGETS:
            cmd = ["uvx", MATURIN, "build", "--release", "--target", target]
            cmd += ["-m", str(CRATES / "dsp-py" / "Cargo.toml"), "-o", tmp]
            subprocess.run(cmd, cwd=CRATES, env=env, check=True)
        built = sorted(Path(tmp).glob("*.whl"))
        if len(built) != len(TARGETS):
            sys.exit(f"expected {len(TARGETS)} wheels, got {[w.name for w in built]}")
        WHEELS.mkdir(exist_ok=True)
        for old in WHEELS.glob("*.whl"):
            old.unlink()
        for w in built:
            shutil.copy2(w, WHEELS / w.name)
    STAMP.write_text(stamp + "\n", encoding="utf-8")


def dev_install() -> None:
    """Iterating on the Rust: build this machine's target only, install it
    into .venv, leave crates/wheels/ alone. Stamped "dev", which
    tests/test_native.py accepts for the loaded module — the committed
    wheels still have to be rebuilt (no flag) before a commit."""
    host = "aarch64-pc-windows-msvc" if _is_arm() else "x86_64-pc-windows-msvc"
    cargo_bin = Path.home() / ".cargo" / "bin"
    env = {
        **os.environ,
        "SYNTHLAB_DSP_SOURCE": "dev",
        "PATH": f"{cargo_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["uvx", MATURIN, "build", "--release", "--target", host]
        cmd += ["-m", str(CRATES / "dsp-py" / "Cargo.toml"), "-o", tmp]
        subprocess.run(cmd, cwd=CRATES, env=env, check=True)
        (wheel,) = Path(tmp).glob("*.whl")
        subprocess.run(
            ["uv", "pip", "install", "--reinstall", "--no-deps", str(wheel)],
            cwd=ROOT,
            check=True,
        )


def _is_arm() -> bool:
    import platform

    return platform.machine().lower() in ("arm64", "aarch64")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check", action="store_true", help="exit 1 if the wheels are stale"
    )
    ap.add_argument(
        "--dev",
        action="store_true",
        help="build this machine's target only and install it (no commit)",
    )
    args = ap.parse_args()
    if args.dev:
        dev_install()
        return
    stamp = source_hash()
    if args.check:
        if recorded() != stamp:
            sys.exit(f"stale: wheels built from {recorded() or '?'}, sources {stamp}")
        print(f"wheels match the sources ({stamp})")
        return
    build(stamp)
    print(f"built {', '.join(w.name for w in sorted(WHEELS.glob('*.whl')))}")
    # The wheel keeps its filename across builds, so uv has to be told:
    # uv.lock pins each wheel's sha256 (the other machines verify it on
    # install) and won't re-read a changed local wheel on its own — without
    # the refresh, `uv sync` stops on a hash mismatch.
    for cmd in (
        ["uv", "lock", "--refresh-package", "synthlab-dsp"],
        ["uv", "sync", "--all-extras", "--reinstall-package", "synthlab-dsp"],
    ):
        subprocess.run(cmd, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
