"""The compute-*.yml workflows take part in automatic propagation (ops/RELEASE.md, "Compute workflows adopt it").

For each compute image workflow: it runs on the release tags, builds only when the image's inputs changed (inputs hash, then
ops/ci/build_or_reuse.sh check / tag), tags a new image only after its smoke test, reports the reused or built digest, and
uploads the `*-digests` artifact release.py reads (digests.json = {tag, commit, images}) only after every check passed. The
image's row in ops/image_inputs.json names everything its build reads that a COPY line does not show.
Nothing builds or runs an image: the files are read and compared. Skipped when PyYAML is missing.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS.parent))
from ops.release_lib import inputs  # noqa: E402

yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
WF_DIR = OPS / "github" / "workflows"
SPEC = inputs.load_spec()

# workflow file -> (image, artifact release.py reads, jobs that must pass before digests.json is written)
ADOPTED = {
    "compute-images.yml": ("compute-mentat", "compute-images-digests", ["build", "sbom", "scan"]),
    "compute-office.yml": ("office-calc", "compute-office-digests", ["build", "sbom", "scan"]),
    "compute-melange.yml": ("compute-melange", "compute-melange-digests", ["build", "smoke", "sbom", "scan"]),
    "compute-collector.yml": ("compute-collector", "compute-collector-digests", ["build", "sbom", "scan"]),
}
NOT_REUSED = "steps.reuse.outputs.reused != 'true'"


def load(name):
    text = (WF_DIR / name).read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def steps(wf, job="build"):
    return wf["jobs"][job]["steps"]


def index(all_steps, **match):
    found = [i for i, s in enumerate(all_steps) if all(s.get(k) == v for k, v in match.items())]
    assert len(found) == 1, (match, found)
    return found[0]


def run_of(step):
    return step.get("run", "")


@pytest.mark.parametrize("name", sorted(ADOPTED))
def test_the_workflow_runs_on_the_release_tags_and_by_hand(name):
    _, wf = load(name)
    trigger = wf.get("on") or wf.get(True)
    assert trigger["push"] == {"tags": ["v*"]} and "workflow_dispatch" in trigger     # release.py waits for every run of the tagged commit
    assert wf["permissions"] == {}


@pytest.mark.parametrize("name", sorted(ADOPTED))
def test_the_image_is_reused_when_its_inputs_are_unchanged_and_tagged_only_after_its_smoke_test(name):
    image, _, _ = ADOPTED[name]
    text, wf = load(name)
    build = steps(wf)
    i_inputs, i_reuse, i_push = index(build, id="inputs"), index(build, id="reuse"), index(build, id="push")
    i_image = index(build, id="image")
    assert i_inputs < i_reuse < i_push < i_image
    assert f"ops/release_lib/inputs.py hash {image}" in run_of(build[i_inputs])
    assert "ops/ci/build_or_reuse.sh check " in run_of(build[i_reuse]) and "steps.inputs.outputs.hash" in json.dumps(build[i_reuse]["env"])
    assert build[i_push]["if"] == NOT_REUSED                                           # a hit builds nothing
    digest = build[i_image]["env"]["DIGEST"]                                           # the reused digest, else the one just pushed
    assert "steps.reuse.outputs.digest" in digest and "steps.push.outputs.digest" in digest
    assert wf["jobs"]["build"]["outputs"]["digest"] == "${{ steps.image.outputs.digest }}"
    tag = [i for i, s in enumerate(build) if "build_or_reuse.sh tag " in run_of(s)]
    assert len(tag) == 1 and build[tag[0]]["if"] == NOT_REUSED and tag[0] > i_image
    smokes = [i for i, s in enumerate(build) if s.get("name", "").lower().startswith("smoke")]
    assert all(tag[0] > i for i in smokes)                                             # a failed smoke test leaves nothing to reuse
    assert "steps.push.outputs.digest" not in text.replace(digest, "")                 # every later use goes through steps.image
    assert re.search(rf'build_or_reuse\.sh tag ("\$IMAGE_NAME"|"\$IMAGE_REPO"|ghcr\.io/pjg93-222/{image}) "\$HASH" "\$DIGEST"', run_of(build[tag[0]]))


@pytest.mark.parametrize("name", sorted(ADOPTED))
def test_digests_json_is_written_only_after_every_check_and_uploaded_under_the_documented_name(name):
    image, artifact, needs = ADOPTED[name]
    _, wf = load(name)
    job = wf["jobs"]["digests"]
    assert job["needs"] == needs and job["permissions"] == {"contents": "read"}
    write, upload = job["steps"]
    script = run_of(write)
    assert "needs.build.outputs.digest" in json.dumps(write["env"]) and "needs.build.outputs.tag" in json.dumps(write["env"])
    assert "@sha256:[0-9a-f]{64}" in script                                           # no digest, no file
    assert f'images: {{"{image}": $' in script and "tag: $tag, commit: $sha" in script
    assert upload["with"] == {"name": artifact, "path": "digests.json"} and artifact.endswith("-digests")


@pytest.mark.parametrize("name", sorted(ADOPTED))
def test_only_the_digests_job_names_the_artifact_release_py_reads_and_no_job_but_build_may_push(name):
    _, artifact, _ = ADOPTED[name]
    text, wf = load(name)
    assert text.count(f"name: {artifact}") == 1
    assert all(j["permissions"].get("packages") != "write" for n, j in wf["jobs"].items() if n != "build")


def entries_of_tree():
    """{path: (mode, oid)} of the files under ops/ as they are on disk (the compute images' inputs are all there)."""
    return {p.relative_to(OPS.parent).as_posix(): ("100644", hashlib.sha1(p.read_bytes()).hexdigest()) for p in OPS.rglob("*") if p.is_file()}


@pytest.mark.parametrize("image", sorted(image for image, _, _ in ADOPTED.values()))
def test_the_image_has_a_row_whose_inputs_all_exist_and_cover_what_its_build_reads_without_a_copy(image):
    row = SPEC["images"][image]
    entries = entries_of_tree()
    read = lambda path: (OPS.parent / path).read_text(encoding="utf-8")
    files = set(inputs.input_files(entries, image, SPEC, read))                       # raises when a COPY, common or extra path is missing
    dockerfile = read(row["dockerfile"])
    bound = re.findall(r"--mount=type=bind,source=([^,\s]+)", dockerfile)             # a build mount is an input a COPY line does not show
    assert set(bound) <= files, sorted(set(bound) - files)
    assert "ops/images.lock" in files and row["dockerfile"] in files
    assert re.fullmatch(r"[0-9a-f]{64}", inputs.image_hash(entries, image, SPEC, read))


def test_a_lock_the_workflow_turns_into_build_args_is_an_input_of_its_image():
    """compute-melange's workflow reads the base pins from ops/compute/melange-base.lock (the Dockerfile has defaults, a test keeps them equal)."""
    text, _ = load("compute-melange.yml")
    assert "ops/compute/melange-base.lock" in text
    assert "ops/compute/melange-base.lock" in SPEC["images"]["compute-melange"]["extra"]
    assert {"ops/locks/r-packages.txt", "ops/locks/r-package-repos.txt", "ops/locks/compute-mentat.linux.txt"} <= set(SPEC["images"]["compute-mentat"]["extra"])


def test_the_tree_the_images_run_on_is_not_an_input_so_a_commit_that_touches_only_code_reuses_every_compute_image():
    for image in ("compute-mentat", "compute-melange", "compute-collector", "office-calc"):
        entries = entries_of_tree()
        read = lambda path: (OPS.parent / path).read_text(encoding="utf-8")
        files = inputs.input_files(entries, image, SPEC, read)
        assert not [f for f in files if f.startswith(("fec/", "mentat/"))], image
