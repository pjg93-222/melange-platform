"""The current release on this laptop: the signed manifest every compute job resolves its image through.

`%LOCALAPPDATA%\\Melange\\releases\\current\\manifest.json` (+ `.sig`) is written by the release command after the manifest
was signed and verified, and by `release.py current --use <id>` for a rollback. A job never reads a hand-edited lock: it asks
for an image by name and gets `repo@sha256:...` only if the manifest verifies against ops/vps/allowed_signers. Anything else
(no manifest, bad signature, no such image, no allowed_signers) raises and the job does not run.

    python ops/release_lib/current.py image <name>     prints repo@sha256:... or exits 1 with the reason on stderr
    python ops/release_lib/current.py show             release id, kind and every image of the current manifest
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):                          # run by path from a wrapper: make `ops` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.release_lib import core, manifest        # noqa: E402
from ops.release_lib.core import NotConfigured, ReleaseError    # noqa: E402

FILES = ("manifest.json", "manifest.json.sig")


def current_dir(cfg) -> Path:
    return core.releases_root(cfg) / "current"


def allowed_signers(cfg) -> Path:
    path = Path(cfg["signing"].get("allowed_signers") or "")
    path = path if path.is_absolute() else core.OPS.parent / path
    if not path.is_file():
        raise NotConfigured(f"signing.allowed_signers {cfg['signing'].get('allowed_signers')!r} is not a file: the current manifest cannot be verified")
    return path


def publish(cfg, release_dir) -> dict:
    """Verify <release_dir>/manifest.json(+.sig) and make it the current manifest. The signature is written before the manifest;
    a reader that lands between the two replaces fails verification and retries (see load), never trusts a mixed pair."""
    release_dir = Path(release_dir)
    s = cfg["signing"]
    result = manifest.verify_manifest(release_dir / "manifest.json", allowed_signers(cfg), principal=s["principal"], namespace=s["namespace"])
    for name in reversed(FILES):
        core.atomic_write(current_dir(cfg) / name, (release_dir / name).read_bytes())
    return result["manifest"]


def load(cfg, retries=2) -> dict:
    """The current manifest, verified. Fail closed: ReleaseError / NotConfigured when it cannot be."""
    path = current_dir(cfg) / "manifest.json"
    s = cfg["signing"]
    for attempt in range(retries + 1):
        if not path.is_file():
            raise ReleaseError("NO_CURRENT", f"{path} does not exist: no release has been published on this laptop")
        try:
            return manifest.verify_manifest(path, allowed_signers(cfg), principal=s["principal"], namespace=s["namespace"])["manifest"]
        except ReleaseError as error:
            if attempt == retries or error.code not in ("SIGNATURE_BAD", "SIGNATURE_MISSING"):
                raise
            time.sleep(0.3)                             # a publish was replacing the pair


def images_of(doc: dict) -> dict:
    """{name: ref}. A manifest from before the `images` map names the three app images in `systems`."""
    return dict(doc["images"]) if doc.get("images") else {manifest.IMAGE_NAMES[k]: v["image"] for k, v in doc["systems"].items()}


def image_for(name: str, cfg=None) -> str:
    cfg = cfg or core.load_config()
    refs = images_of(load(cfg))
    if name not in refs:
        raise ReleaseError("NO_SUCH_IMAGE", f"{name!r} is not in the current manifest (has: {', '.join(sorted(refs))})")
    if not manifest.IMAGE_RE.fullmatch(refs[name]):
        raise ReleaseError("MANIFEST_INVALID", f"{name} is not pinned by digest")
    return refs[name]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(argv) == 2 and argv[0] == "image":
            print(image_for(argv[1]))
        elif argv == ["show"]:
            doc = load(core.load_config())
            print(f"{doc['release_id']} ({doc['kind']}), platform {doc['platform_repo']['tag']}")
            for name, ref in sorted(images_of(doc).items()):
                print(f"  {name}: {ref}")
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (ReleaseError, ValueError, OSError) as error:        # anything unreadable is a refusal, never a guess
        print(f"current: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
