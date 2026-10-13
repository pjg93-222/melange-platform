"""ops/release_lib/inputs.py: the per-image input hash CI uses to decide "reuse" or "build".
Standard library only, so it also runs on the Linux CI runner (the same code computes the hash there)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ops.release_lib import inputs   # noqa: E402

DOCKERFILE = """# syntax=docker/dockerfile:1.7
ARG PYTHON_BASE=python:3.12
FROM ${PYTHON_BASE} AS src
COPY mentat/app /stage/app
COPY --chown=1:1 mentat/tools /stage/tools
FROM ${PYTHON_BASE}
COPY --from=src /stage/app /app/mentat/app
COPY ops/locks/x.txt \\
     ops/locks/y.txt /tmp/
ADD https://example.invalid/tool.tgz /opt/
COPY --chmod=0755 ops/compute/run.sh /usr/local/bin/run
COPY <<EOF /etc/inline
inline
EOF
"""
SPEC = {"schema": 1, "common": ["ops/images.lock"], "images": {
    "web": {"dockerfile": "ops/docker/web.Dockerfile"},
    "job": {"dockerfile": "ops/docker/job.Dockerfile", "extra": ["ops/compute/mounted/*.py"]},
    "later": {"dockerfile": "ops/docker/later.Dockerfile"}}}


def tree(**changes):
    """{path: (mode, oid)}: the oid is a stand-in for the content, so changing a file changes its oid."""
    files = {"ops/docker/web.Dockerfile": DOCKERFILE, "ops/docker/job.Dockerfile": "FROM x\nCOPY ops/compute/run.sh /run\n",
             "ops/images.lock": "{}", "mentat/app/server.py": "a", "mentat/app/sub/more.py": "b", "mentat/tools/t.py": "c",
             "ops/locks/x.txt": "x", "ops/locks/y.txt": "y", "ops/compute/run.sh": "r", "ops/compute/mounted/m.py": "m",
             "fec/unrelated.py": "u"}
    files.update(changes)
    return {path: ("100644", hashlib.sha1(text.encode()).hexdigest()) for path, text in files.items() if text is not None}


def reader(**changes):
    texts = {"ops/docker/web.Dockerfile": DOCKERFILE, "ops/docker/job.Dockerfile": "FROM x\nCOPY ops/compute/run.sh /run\n"}
    texts.update({k: v for k, v in changes.items() if k in texts})
    return lambda path: texts[path]


class CopySourceTests(unittest.TestCase):
    def test_copy_and_add_sources_are_found_and_stage_copies_urls_and_heredocs_are_not(self):
        self.assertEqual(inputs.copy_sources(DOCKERFILE),
                         ["mentat/app", "mentat/tools", "ops/locks/x.txt", "ops/locks/y.txt", "ops/compute/run.sh"])

    def test_a_pattern_covers_a_file_a_directory_a_glob_or_everything(self):
        entries = tree()
        self.assertEqual(inputs.match(entries, "mentat/app"), ["mentat/app/server.py", "mentat/app/sub/more.py"])
        self.assertEqual(inputs.match(entries, "./mentat/app/"), ["mentat/app/server.py", "mentat/app/sub/more.py"])
        self.assertEqual(inputs.match(entries, "ops/locks/x.txt"), ["ops/locks/x.txt"])
        self.assertEqual(inputs.match(entries, "ops/compute/mounted/*.py"), ["ops/compute/mounted/m.py"])
        self.assertEqual(inputs.match(entries, "."), sorted(entries))
        self.assertEqual(inputs.match(entries, "mentat/ap"), [])                    # a prefix of a name is not a directory


class HashTests(unittest.TestCase):
    def h(self, name="web", entries=None, **changes):
        return inputs.image_hash(entries or tree(**changes), name, SPEC, reader(**changes))

    def test_the_hash_is_stable_and_ignores_the_order_files_arrive_in(self):
        entries = tree()
        shuffled = dict(reversed(list(entries.items())))
        self.assertEqual(self.h(entries=entries), self.h(entries=shuffled))
        self.assertRegex(self.h(), r"^[0-9a-f]{64}$")
        self.assertNotEqual(self.h("web"), self.h("job"))                           # the image name is part of it

    def test_every_listed_input_changes_it(self):
        base = self.h()
        for path in ("ops/docker/web.Dockerfile", "mentat/app/sub/more.py", "mentat/tools/t.py", "ops/locks/y.txt",
                     "ops/compute/run.sh", "ops/images.lock"):
            with self.subTest(path=path):
                text = DOCKERFILE + "\n# edit" if path.endswith("Dockerfile") else "changed"
                self.assertNotEqual(self.h(**{path: text}), base)

    def test_a_new_file_in_a_copied_directory_changes_it(self):
        self.assertNotEqual(self.h(**{"mentat/app/new.py": "n"}), self.h())

    def test_what_the_image_does_not_use_cannot_change_it(self):
        base = self.h()
        self.assertEqual(self.h(**{"fec/unrelated.py": "other"}), base)
        self.assertEqual(self.h(**{"ops/compute/mounted/m.py": "other"}), base)      # web does not list that extra
        self.assertEqual(self.h(**{"mentat/tests/t.py": "new"}), base)

    def test_extra_paths_a_wrapper_mounts_are_inputs_of_that_image_only(self):
        self.assertNotEqual(self.h("job", **{"ops/compute/mounted/m.py": "other"}), self.h("job"))

    def test_an_input_that_matches_nothing_stops_instead_of_hashing_less(self):
        entries = tree(**{"ops/locks/x.txt": None})
        with self.assertRaises(inputs.InputsError):
            inputs.image_hash(entries, "web", SPEC, reader())
        with self.assertRaises(inputs.InputsError):
            inputs.image_hash(tree(), "nope", SPEC, reader())

    def test_images_whose_dockerfile_is_not_in_the_tree_yet_are_skipped(self):
        self.assertEqual(sorted(inputs.all_hashes(tree(), SPEC, reader())), ["job", "web"])


class GitTests(unittest.TestCase):
    """The laptop hashes the tree it exports, CI hashes `git ls-tree` of the tag with this same script: they must agree."""

    def test_the_script_in_a_checkout_equals_the_hash_from_the_entries(self):
        with tempfile.TemporaryDirectory(prefix="inputs-") as tmp:
            repo = Path(tmp)
            spec = {"schema": 1, "common": ["ops/images.lock"], "images": {"web": {"dockerfile": "ops/docker/web.Dockerfile"}}}
            files = {"ops/docker/web.Dockerfile": "FROM x\nCOPY mentat/app /a\n", "ops/images.lock": "{}\n", "mentat/app/s.py": "print(1)\r\n",
                     "ops/image_inputs.json": json.dumps(spec)}
            for name, text in files.items():
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_bytes(text.encode())
            script = repo / "ops" / "release_lib" / "inputs.py"                       # as CI has it: next to ../image_inputs.json
            script.parent.mkdir(parents=True)
            shutil.copyfile(REPO / "ops" / "release_lib" / "inputs.py", script)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            for argv in (["init", "-q"], ["-c", "core.autocrlf=false", "add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-qm", "x"]):
                subprocess.run(["git", *argv], cwd=repo, env=env, check=True, capture_output=True)
            entries = inputs.git_entries("HEAD", cwd=repo)
            want = inputs.image_hash(entries, "web", spec, inputs.git_reader(entries, cwd=repo))
            run = subprocess.run([sys.executable, str(script), "hash", "web"], cwd=repo, capture_output=True, text=True)
            self.assertEqual((run.returncode, run.stdout.strip()), (0, want), run.stderr)
            self.assertEqual(entries["mentat/app/s.py"][0], "100644")
            bad = subprocess.run([sys.executable, str(script), "hash", "no-such-image"], cwd=repo, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)                                       # an unknown image stops the CI step

    def test_the_committed_spec_names_the_two_app_images_and_hashes_them_from_this_tree(self):
        spec = inputs.load_spec()
        self.assertTrue({"melange-desk", "mentat-console"} <= set(spec["images"]))
        entries = inputs.git_entries("HEAD", cwd=REPO)
        if "ops/image_inputs.json" not in entries:
            self.skipTest("ops/image_inputs.json is not committed yet")
        read = inputs.git_reader(entries, cwd=REPO)
        self.assertRegex(inputs.image_hash(entries, "melange-desk", spec, read), "^[0-9a-f]{64}$")
        try:
            inputs.image_hash(entries, "mentat-console", spec, read)
        except inputs.InputsError as error:                  # the Melange repo alone has no mentat/ (the export merges Mentat in)
            self.assertIn("mentat/", str(error))


if __name__ == "__main__":
    unittest.main()
