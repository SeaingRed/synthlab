"""Write synthlab/kits/naked_drums.json from a checkout of the kit.

    git clone https://github.com/sfzinstruments/WilkinsonAudio.NakedDrums <dir>
    git -C <dir> checkout 407732a548606ac715ad8f3fbac63d9f4df77c0d
    uv run python tools/make_kit_manifest.py <dir>

The manifest is text (path, size, SHA-256 of each file the studio needs);
no audio enters the repo. `synthlab.kits.fetch` downloads against it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from synthlab.kits.fetch import NAKED

COMMIT = "407732a548606ac715ad8f3fbac63d9f4df77c0d"
ROOT = "Wilkinson Audio/Naked Drums"
OUT = Path(__file__).resolve().parents[1] / "synthlab" / "kits" / NAKED.manifest


def wanted(root: Path) -> list[str]:
    paths = {"LICENSE", "README.md"}
    for p in (root / ROOT / "Data").rglob("*"):
        if p.is_file():
            paths.add(p.relative_to(root).as_posix())
    for name in ("Naked Drums GM keymap.txt", "Naked Drums GM.sfz"):
        paths.add(f"{ROOT}/User/{name}")
    for p in (root / ROOT / "Samples").glob("*.flac"):
        paths.add(p.relative_to(root).as_posix())
    return sorted(paths)


def entry(root: Path, rel: str) -> dict:
    data = (root / rel).read_bytes()
    return {"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    files = [entry(root, rel) for rel in wanted(root)]
    doc = {
        "commit": COMMIT,
        "base_url": NAKED.base_url,
        "license": "CC-BY-4.0",
        "credit": "Naked Drums by Wilkinson Audio, CC-BY 4.0",
        "files": files,
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    total = sum(f["size"] for f in files)
    print(f"{len(files)} files, {total / 1e6:.0f} MB -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
