#!/usr/bin/env python3
"""Embed the VPS payload (updater, host scripts, systemd units, compose files, host-files.toml) in cloud-init.yaml.

cloud-init user-data is one pasted file, so the payload travels inside it as a xz+base64
tarball between the BEGIN/END PAYLOAD markers. The sources stay the only place to edit.

    python ops/vps/build_cloud_init.py           rewrite the payload block
    python ops/vps/build_cloud_init.py --check   exit 1 if cloud-init.yaml is stale (CI/test)
"""
from __future__ import annotations

import base64
import io
import lzma
import re
import sys
import tarfile
import tomllib
from pathlib import Path

OPS = Path(__file__).resolve().parents[1]
TARGET = OPS / "vps" / "cloud-init.yaml"
BLOCK = re.compile(r"(  # BEGIN PAYLOAD[^\n]*\n)(.*?)(  # END PAYLOAD)", re.S)


def members() -> dict[str, tuple[bytes, int]]:
    """Path inside the tar (relative to /) -> (content, mode). CRLF-normalised: a Windows
    checkout must not change what a Linux server receives."""
    src = {
        "usr/local/lib/melange/melange_update.py": ("vps/melange_update.py", 0o755),
        "usr/local/lib/melange/melange-ops.sh": ("vps/melange-ops.sh", 0o755),
        "usr/local/sbin/melange-bootstrap": ("vps/melange-bootstrap.sh", 0o750),
        "srv/serve/compose.yaml": ("compose.yaml", 0o644),
        "srv/serve/compose.env.example": ("compose.env.example", 0o644),
    }
    for p in sorted((OPS / "vps" / "systemd").iterdir()):
        src[f"etc/systemd/system/{p.name}"] = (f"vps/systemd/{p.name}", 0o644)
    out = {name: ((OPS / rel).read_bytes().replace(b"\r\n", b"\n"), mode) for name, (rel, mode) in src.items()}
    # static host configuration (sshd, Docker daemon, audit...): xz'd here it costs a third of what plain write_files would
    for path, f in tomllib.loads((OPS / "vps" / "host-files.toml").read_text(encoding="utf-8").replace("\r\n", "\n")).items():
        out[path.lstrip("/")] = (f["text"].encode(), f["mode"])
    return dict(sorted(out.items()))


def payload_b64() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, (data, mode) in members().items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    raw = base64.b64encode(lzma.compress(buf.getvalue(), format=lzma.FORMAT_XZ, preset=9 | lzma.PRESET_EXTREME)).decode()
    # 2 KiB lines: every wrap costs 7 bytes of the 32 KiB user-data budget (76-column lines cost about 2 KB)
    return "\n".join("      " + raw[i:i + 2048] for i in range(0, len(raw), 2048)) + "\n"


def embedded(text: str) -> dict[str, tuple[bytes, int]]:
    """What the pasted file would unpack to (compares content, not compressed bytes: xz
    versions differ between machines)."""
    body = BLOCK.search(text).group(2).split("content: |\n", 1)[1]
    raw = lzma.decompress(base64.b64decode("".join(body.split())))
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        return {m.name: (tar.extractfile(m).read(), m.mode) for m in tar.getmembers()}


def render(text: str) -> str:
    body = ("  - path: /opt/melange/payload.tar.xz\n    permissions: \"0600\"\n    encoding: b64\n    content: |\n"
            + payload_b64())
    if not BLOCK.search(text):
        raise SystemExit("BEGIN/END PAYLOAD markers not found in cloud-init.yaml")
    return BLOCK.sub(lambda m: m.group(1) + body + m.group(3), text, count=1)


def main(argv: list[str]) -> int:
    text = TARGET.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "--check" in argv:
        if embedded(text) != members():
            print("cloud-init.yaml payload is stale: run python ops/vps/build_cloud_init.py", file=sys.stderr)
            return 1
        return 0
    new = render(text)
    TARGET.write_text(new, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET} ({len(new.encode()) / 1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
