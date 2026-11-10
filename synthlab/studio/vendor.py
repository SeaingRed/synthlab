"""Browser files the repository does not ship, fetched from the npm registry.

GSAP drives the studio's motion. Its licence (GreenSock's Standard "No
Charge" License) lets anyone use it but is not an open-source licence, so
the repository does not redistribute the file. Instead `synthlab-studio`
fetches the official npm package once, on first launch, checks it against
the integrity hash npm publishes for that exact version, and writes
`static/gsap.min.js` (git-ignored). `python -m synthlab.studio.vendor` does
the same by hand.

Without the file the studio still runs: `base.html` leaves the script out
and every island that animates checks `window.gsap` first, so pages simply
swap without the motion.
"""

from __future__ import annotations

import base64
import hashlib
import io
import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

STATIC = Path(__file__).parent / "static"


@dataclass(frozen=True)
class NpmFile:
    """One file out of one exact npm package version."""

    name: str  # the file written into static/
    package: str
    version: str
    integrity: str  # npm's `dist.integrity` for the tarball
    member: str  # the file inside the tarball
    sha256: str  # the extracted file's own hash, checked again

    @property
    def tarball(self) -> str:
        return f"https://registry.npmjs.org/{self.package}/-/{self.package}-{self.version}.tgz"


GSAP = NpmFile(
    name="gsap.min.js",
    package="gsap",
    version="3.13.0",
    integrity=(
        "sha512-QL7MJ2WMjm1PHWsoFrAQH/J8wUeqZvMtHO58qdekHpCfhvhSL4gSiz6vJf5Ee"
        "MP0LOn3ZCprL2ki/gjED8ghVw=="
    ),
    member="package/dist/gsap.min.js",
    sha256="96c01b81f44a3290e2b4532f55e2c9534b2adc43273a19f3756b2cb41f0fd0b6",
)
FILES = (GSAP,)


def _download(url: str, timeout: float = 30.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - a pinned https URL
        return r.read()


def _verified(f: NpmFile, tgz: bytes) -> bytes:
    alg, want = f.integrity.split("-", 1)
    if base64.b64encode(hashlib.new(alg, tgz).digest()).decode() != want:
        raise ValueError(
            f"{f.package}-{f.version}.tgz does not match npm's integrity hash"
        )
    with tarfile.open(fileobj=io.BytesIO(tgz)) as t:
        member = t.extractfile(f.member)
        if member is None:
            raise ValueError(f"{f.member} is not a file in {f.package}-{f.version}.tgz")
        data = member.read()
    if hashlib.sha256(data).hexdigest() != f.sha256:
        raise ValueError(f"{f.member} does not match its pinned sha256")
    return data


def fetch(f: NpmFile, static: Path = STATIC) -> Path:
    """Download, verify and write one file; raises on any failure."""
    data = _verified(f, _download(f.tarball))
    dest = static / f.name
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def ensure(static: Path = STATIC, out=sys.stderr) -> None:
    """Fetch whatever is missing; never stops the studio from starting."""
    for f in FILES:
        if (static / f.name).exists():
            continue
        print(f"fetching {f.package} {f.version} from the npm registry ...", file=out)
        try:
            fetch(f, static)
        except Exception as err:  # offline, a proxy, a changed file: run without it
            print(
                f"could not fetch {f.package} ({err}); the studio runs without "
                f"its motion. Retry with: python -m synthlab.studio.vendor",
                file=out,
            )


def main() -> int:
    for f in FILES:
        print(f"{f.package} {f.version} -> {fetch(f)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
