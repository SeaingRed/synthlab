"""Build the live voice's WebAssembly module (synthlab/studio/wasm/ ->
synthlab/studio/static/voice-dsp.wasm) with the pinned AssemblyScript.

    uv run python tools/build_wasm.py           # npm ci if needed, build, record sha256
    uv run python tools/build_wasm.py --check   # rebuild to a temp file, compare

Needs node + npm on PATH; installs nothing outside synthlab/studio/wasm/
(`npm ci` puts the exact locked toolchain in its node_modules/, which git
ignores). Running the studio never needs any of this: the built module is
committed, and tests/studio/test_voice_wasm.py checks it against the
recorded sha256 (always) and against a fresh build (when node_modules/
exists).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "synthlab/studio/wasm"
OUT = ROOT / "synthlab/studio/static/voice-dsp.wasm"


def _npm(*args: str) -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("npm isn't on PATH: install node (https://nodejs.org) first")
    subprocess.run([npm, *args], cwd=SRC, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="compare a fresh build")
    args = ap.parse_args()
    if not (SRC / "node_modules/assemblyscript").exists():
        _npm("ci")
    if not args.check:
        _npm("run", "build")
        print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size} bytes")
        return
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "voice-dsp.wasm"
        subprocess.run(["node", "build.mjs", str(fresh)], cwd=SRC, check=True)
        same = fresh.read_bytes() == OUT.read_bytes()
    recorded = (SRC / "voice-dsp.wasm.sha256").read_text(encoding="utf-8").strip()
    served = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"fresh build == committed: {same}; recorded == served: {recorded == served}")
    sys.exit(0 if same and recorded == served else 1)


if __name__ == "__main__":
    main()
