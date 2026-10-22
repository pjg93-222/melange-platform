# -*- coding: utf-8 -*-
"""Stage 5 model hash: an unreadable weight file is a named refusal (weekly 10-18 HARD FAILURE).

A OneDrive online-only ``model.safetensors`` whose hydration stalled raised a bare
``OSError: [Errno 22]`` out of ``model_dir_sha256``.  It is now a :class:`Stage5Refused` that names the file and
the ``attrib`` remedy.  Synthetic tmp_path models only; nothing loads a model or touches Draft Systems.
"""
from __future__ import annotations

import builtins
import errno
import hashlib
from pathlib import Path
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from stance import press_stage5 as S


def make_model(tmp_path):
    model = tmp_path / "debate_finetuned"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"weights-v1")
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model / ".Rhistory").write_text("noise", encoding="utf-8")
    return model


def test_an_unreadable_weight_file_is_a_stage5_refusal_naming_the_file_and_the_remedy(tmp_path, monkeypatch):
    model = make_model(tmp_path)
    real_open = builtins.open

    def flaky_open(file, *args, **kwargs):
        if Path(file).name == "model.safetensors":
            raise OSError(errno.EINVAL, "Invalid argument")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(S, "open", flaky_open, raising=False)
    with pytest.raises(S.Stage5Refused) as caught:
        S.model_dir_sha256(model)
    message = str(caught.value)
    assert "model.safetensors" in message and "OneDrive online-only" in message
    assert 'attrib +P -U "%s\\*" /S /D' % model in message
    assert isinstance(caught.value.__cause__, OSError) and caught.value.__cause__.errno == errno.EINVAL


def test_a_read_that_fails_mid_file_is_refused_the_same_way(tmp_path, monkeypatch):
    model = make_model(tmp_path)
    real_open = builtins.open

    class Stalled:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

        def read(self, size):
            raise OSError(errno.EINVAL, "Invalid argument")

    def stalled_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        return Stalled(handle) if Path(file).name == "model.safetensors" else handle

    monkeypatch.setattr(S, "open", stalled_open, raising=False)
    with pytest.raises(S.Stage5Refused, match="model.safetensors"):
        S.model_dir_sha256(model)


def test_the_model_hash_of_a_readable_tiny_dir_is_unchanged(tmp_path):
    model = make_model(tmp_path)
    (model / "sub").mkdir()
    expected = hashlib.sha256()
    for name in ("config.json", "model.safetensors", "tokenizer.json"):        # sorted; hidden files and dirs skipped
        data = (model / name).read_bytes()
        expected.update(("%s\0%d\0%s\n" % (name, len(data), hashlib.sha256(data).hexdigest())).encode("utf-8"))
    assert S.model_dir_sha256(model) == expected.hexdigest()
