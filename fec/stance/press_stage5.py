# -*- coding: utf-8 -*-
"""Stage 5 for the press refresh feed (owner ruling D-219, Paul 2026-10-15).

New press rows are scored with the ratified Stage 5, not with the retired
``press_refresh_rules_v1`` polarity guess: the LOCAL fine-tuned Political DEBATE model, Paul's
human-label overlay and the Lexicoder LSD tone, producing the same columns the Draft Systems
``cu_press_stance_tone.csv`` carries (run_stage5.R:43,73-76,88-159 and stage5_debate.py).

Nothing here re-implements the models.  DEBATE runs through Draft Systems' own
``stage5_debate.py`` sidecar in its own ``.venv_debate`` (transformers pipeline, CPU, 3000
characters), and the LSD tone through Draft Systems' own ``stage5_lsd.R``; this module only
feeds them rows and applies run_stage5.R's aggregation: human overlay over DEBATE, then
``press_value = final_sign x intensity`` and ``score_graded``/``score_label`` rewritten to the
final sign.  Both heavy steps run at below-normal process priority.

Guards:
  * ``CU_S5_DEBATE_MODEL`` must resolve to the local ``debate_finetuned`` directory.  When it is unset
    (scheduled runners do not carry it) the model is that directory under the Draft Systems root
    (``DRAFT``); if it is missing or incomplete, that is a refusal.  A value naming another directory or
    the base HuggingFace model is a refusal too (:class:`Stage5Refused`); the sidecar's own default is the
    base model and is never used, because the model is always passed explicitly.
  * Each row is scored once.  A cache keyed by (turn key, sha256 of the scored text, sha256 of
    the model directory) serves it afterwards; a changed body or a new model rescored it.
  * At most ``batch_limit`` rows are newly scored per invocation.  Rows beyond it are WITHHELD
    from the output (counted as pending in the report), never emitted with another scorer's
    label, and are picked up by the next invocation.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bounded_subprocess import run_bounded
from paths import DRAFT, ROOT
from press_stream import canonical_url

STANCE_SRC = "stage5_debate_ft_lsd_v1"
OVERLAY_SRC = "human_label"            # run_stage5.R:139, the audit trail of an overlay hit
MODEL_DIR_NAME = "debate_finetuned"
MAXCHARS = 3000                        # run_stage5.R CU_S5_MAXCHARS default
BATCH = 16                             # run_stage5.R CU_S5_BATCH default
BILL_W = 0.03                          # run_stage5.R CU_S5_BILL_WEIGHT default: intensity of a row with no usable body
MIN_TEXT_CHARS = 300                   # run_stage5.R:136 has_text
MIN_BODY_WORDS = 20                    # the refresh's usable-article rule (refresh_press_delta.R)
DEFAULT_BATCH_ROWS = 400
SIDECAR_TIMEOUT_SECONDS = 4 * 60 * 60
LSD_TIMEOUT_SECONDS = 30 * 60
CACHE_PATH = os.path.join(ROOT, "data", "stance", "press_stage5_cache.jsonl")
SIGN = {"support": 1, "oppose": -1, "neutral": 0}
# run_stage5.R:156-159 .SLAB
SLAB = {-1.0: "Very strongly negative", -0.8: "Strongly negative", -0.6: "Negative",
        -0.4: "Weakly negative", -0.2: "Very weakly negative", 0.0: "Neutral",
        0.2: "Very weakly positive", 0.4: "Weakly positive", 0.6: "Positive",
        0.8: "Strongly positive", 1.0: "Very strongly positive"}
DEBATE_FIELDS = ("stance", "stance_sign", "p_support", "p_oppose", "p_neutral", "stance_conf")
LSD_FIELDS = ("n_words", "lsd_pos", "lsd_neg", "lsd_net", "lsd_net_norm", "lsd_charge")
# stage5_lsd.R finds lsd_dict.rds in the working directory, so the driver changes into the dictionary directory.
LSD_DRIVER = r'''a <- commandArgs(trailingOnly = TRUE)
if (nzchar(a[5]) && dir.exists(a[5])) .libPaths(c(a[5], .libPaths()))
setwd(a[1])
suppressMessages(source(a[2]))
d <- read.csv(a[3], stringsAsFactors = FALSE, fileEncoding = "UTF-8", colClasses = "character")
res <- lsd_tone(enc2utf8(d$text))
write.csv(cbind(id = d$id, res), a[4], row.names = FALSE, fileEncoding = "UTF-8")
'''


class Stage5Refused(RuntimeError):
    """Stage 5 refused to run: the DEBATE model is not the ratified local fine-tuned directory."""


class Stage5Failed(RuntimeError):
    """A Stage 5 sidecar failed or returned an incomplete answer."""


def turn_key(row):
    """The feed's own turn key (build_press_engine_feed.turn_key)."""
    return (canonical_url(row.get("url"), keep_fragment=False),
            (row.get("bioguide") or "").strip().upper())


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def num(value):
    """A number the way R's write.csv prints it ('0', '-0.6', '0.03'); never '-0'."""
    return "%.15g" % (float(value) + 0.0)


def resolve_model_dir(env=None, expected=None):
    """The DEBATE model directory, or :class:`Stage5Refused`.  There is no base-model fallback."""
    env = os.environ if env is None else env
    expected = Path(expected or os.path.join(DRAFT, MODEL_DIR_NAME))
    value = (env.get("CU_S5_DEBATE_MODEL") or "").strip()
    if not value:
        # Owner ruling D-219 (Paul 2026-10-15): the ratified model is the default.  A scheduled runner does not
        # carry the variable, and the only directory it may mean is <DRAFT>/debate_finetuned.
        if not expected.is_dir():
            raise Stage5Refused(
                "CU_S5_DEBATE_MODEL is not set and the default local fine-tuned model %s does not exist; "
                "Stage 5 would fall back to the base HuggingFace model, which is not the ratified scorer" % expected)
        chosen = expected
    else:
        chosen = Path(value)
        if not chosen.is_dir() or not expected.is_dir() or not os.path.samefile(chosen, expected):
            raise Stage5Refused(
                "CU_S5_DEBATE_MODEL=%r does not resolve to the local fine-tuned model %s" % (value, expected))
    missing = [name for name in ("config.json", "tokenizer.json") if not (chosen / name).is_file()]
    if not any((chosen / name).is_file() for name in ("model.safetensors", "pytorch_model.bin")):
        missing.append("model weights")
    if missing:
        raise Stage5Refused("%s is not a complete model directory (missing %s)" % (chosen, ", ".join(missing)))
    return chosen.resolve()


def model_dir_sha256(model_dir):
    """One hash over every non-hidden file's name, size and content (a stray .Rhistory does not count)."""
    digest = hashlib.sha256()
    for path in sorted(Path(model_dir).iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name.startswith("."):
            continue
        file_digest = hashlib.sha256()
        try:
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    file_digest.update(block)
        except OSError as exc:
            # weekly 10-18: a OneDrive online-only model.safetensors whose hydration stalled raised Errno 22 here.
            raise Stage5Refused(
                "cannot read model file %s (%s); it may be a OneDrive online-only file. Pin it with: "
                'attrib +P -U "%s\\*" /S /D' % (path, exc, model_dir)) from exc
        digest.update(("%s\0%d\0%s\n" % (path.name, path.stat().st_size, file_digest.hexdigest())).encode("utf-8"))
    return digest.hexdigest()


def load_overlay(path):
    """Paul's hand labels, exactly run_stage5.R:112-118: url -> label, first occurrence wins, valid labels only."""
    overlay = {}
    if not path or not os.path.isfile(path):
        return overlay
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            label = (row.get("human_label") or "").strip().lower()
            if label in SIGN:
                overlay.setdefault(row.get("url") or "", label)
    return overlay


def resolve_text(row, bodies):
    """(scored text, body, title_fallback): the body when it is usable, else the title (run_stage5.R:72-73)."""
    body = bodies.get(turn_key(row)) or ""
    if len(body.split()) >= MIN_BODY_WORDS:
        return body, body, False
    return row.get("title") or "", "", True


def _low_priority(argv):
    """Below-normal priority for a child: a creation flag on Windows, nice elsewhere (children inherit it)."""
    if os.name == "nt":
        import subprocess
        return list(argv), subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return ["nice", "-n", "10"] + list(argv), 0


def _run(argv, timeout, env=None, cwd=None):
    name = Path(str(argv[1])).name
    argv, flags = _low_priority(argv)
    result = run_bounded(argv, timeout, env=env, cwd=cwd, creationflags=flags)
    if result.timed_out or result.returncode:
        raise Stage5Failed("%s failed (rc %s%s): %s" % (
            name, result.returncode, ", timed out" if result.timed_out else "", (result.stderr or "")[-600:]))
    return result


def run_debate(items, model_dir, *, draft_dir=DRAFT, python=None, script=None,
               batch=BATCH, max_chars=MAXCHARS, timeout=SIDECAR_TIMEOUT_SECONDS):
    """Stage 5a through stage5_debate.py.  ``items`` is ``[(id, text)]``; returns ``{id: DEBATE_FIELDS dict}``."""
    python = python or os.path.join(draft_dir, ".venv_debate", "Scripts",
                                    "python.exe" if os.name == "nt" else "python")
    script = script or os.path.join(draft_dir, "stage5_debate.py")
    for needed in (python, script):
        if not os.path.isfile(needed):
            raise Stage5Failed("DEBATE sidecar file not found: %s" % needed)
    with tempfile.TemporaryDirectory(prefix="press-stage5-") as work:
        inp, out = os.path.join(work, "debate_in.csv"), os.path.join(work, "debate_out.csv")
        with io.open(inp, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "text"])
            writer.writerows(items)
        # The model is always the explicit local directory; offline flags make a stray hub lookup an error, not a download,
        # and no CUDA device is visible (CPU only, as the sidecar itself asks for device=-1).
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "",
               "PYTHONIOENCODING": "utf-8"}
        _run([python, script, "--in", inp, "--out", out, "--model", str(model_dir),
              "--batch", str(batch), "--max_chars", str(max_chars)], timeout, env=env, cwd=draft_dir)
        with io.open(out, encoding="utf-8", newline="") as handle:
            rows = {row["id"]: row for row in csv.DictReader(handle)}
    result = {}
    for item_id, _text in items:
        row = rows.get(item_id)
        if row is None:
            raise Stage5Failed("DEBATE sidecar returned no row for %r" % item_id)
        result[item_id] = {"stance": row["stance"], "stance_sign": int(float(row["stance_sign"])),
                           **{name: float(row[name]) for name in DEBATE_FIELDS[2:]}}
    return result


def run_lsd(items, *, lsd_dir=DRAFT, script=None, rscript=None, timeout=LSD_TIMEOUT_SECONDS):
    """Stage 5b through stage5_lsd.R's own ``lsd_tone``.  ``items`` is ``[(id, text)]``; returns ``{id: LSD_FIELDS dict}``."""
    if rscript is None:
        from collect.refresh_press_stream import find_rscript
        rscript = find_rscript()
    script = script or os.path.join(lsd_dir, "stage5_lsd.R")
    if not os.path.isfile(script):
        raise Stage5Failed("LSD script not found: %s" % script)
    with tempfile.TemporaryDirectory(prefix="press-stage5-") as work:
        inp, out = os.path.join(work, "lsd_in.csv"), os.path.join(work, "lsd_out.csv")
        driver = os.path.join(work, "lsd_driver.R")
        with io.open(inp, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "text"])
            writer.writerows(items)
        with io.open(driver, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(LSD_DRIVER)
        library = os.environ.get("MELANGE_R_LIBRARY") or os.path.join(ROOT, "outputs", "r-library")
        posix = lambda value: str(value).replace("\\", "/")
        _run([rscript, driver, posix(lsd_dir), posix(os.path.abspath(script)), posix(inp), posix(out), posix(library)],
             timeout)
        with io.open(out, encoding="utf-8-sig", newline="") as handle:
            rows = {row["id"]: row for row in csv.DictReader(handle)}
    result = {}
    for item_id, _text in items:
        row = rows.get(item_id)
        if row is None:
            raise Stage5Failed("LSD returned no row for %r" % item_id)
        result[item_id] = {"n_words": int(float(row["n_words"])), "lsd_pos": int(float(row["lsd_pos"])),
                           "lsd_neg": int(float(row["lsd_neg"])), "lsd_net": int(float(row["lsd_net"])),
                           "lsd_net_norm": float(row["lsd_net_norm"]), "lsd_charge": float(row["lsd_charge"])}
    return result


def load_cache(path):
    """{(turn key, text sha256, model sha256): record}.  A torn last line (a crash mid-append) is skipped."""
    cache = {}
    if not path or not os.path.isfile(path):
        return cache
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
                cache[(record["k"], record["t"], record["m"])] = record
            except (ValueError, KeyError, TypeError):
                continue
    return cache


def append_cache(path, records):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def finalize(row, debate, lsd, overlay_label, has_text):
    """run_stage5.R:96-160 for one row: the overlay over DEBATE, press_value, and score_graded/score_label rewritten."""
    stance, sign, src = debate["stance"], int(debate["stance_sign"]), STANCE_SRC
    if overlay_label:                                           # 3b: ground truth beats DEBATE
        stance, sign, src = overlay_label, SIGN[overlay_label], OVERLAY_SRC
    intensity = abs(float(lsd["lsd_net_norm"])) if has_text else BILL_W   # 4: heat only where there is text
    try:
        magnitude = abs(float(row.get("score_graded") or 0.0))
    except ValueError:
        magnitude = 0.0
    graded = 0.0 if sign == 0 else sign * (magnitude if magnitude > 0 else 0.2)   # 4b
    return {
        **row,
        "stance": stance, "stance_sign": str(sign),
        "p_support": num(debate["p_support"]), "p_oppose": num(debate["p_oppose"]),
        "p_neutral": num(debate["p_neutral"]), "stance_conf": num(debate["stance_conf"]),
        "n_words": str(lsd["n_words"]), "lsd_pos": str(lsd["lsd_pos"]), "lsd_neg": str(lsd["lsd_neg"]),
        "lsd_net": str(lsd["lsd_net"]), "lsd_net_norm": num(lsd["lsd_net_norm"]),
        "lsd_charge": num(lsd["lsd_charge"]), "stance_src": src,
        "press_value": num(round(sign * intensity, 6)),
        "score_graded": num(graded), "score_label": SLAB.get(round(graded, 1) + 0.0, ""),
    }


def apply(tone_rows, fulltext_rows, *, cache_path=CACHE_PATH, batch_limit=DEFAULT_BATCH_ROWS, env=None,
          expected_model_dir=None, overlay_path=None, draft_dir=DRAFT, debate_fn=None, lsd_fn=None):
    """Score ``tone_rows`` with Stage 5.  Returns ``(rows with the historical columns, report)``.

    ``fulltext_rows`` supplies each row's body (keyed like the feed's turn key).  A row with no usable
    body is scored on its title.  Rows beyond ``batch_limit`` newly scored ones are withheld and counted
    in ``report['rows_pending']``.  ``debate_fn(items, model_dir)`` / ``lsd_fn(items)`` replace the real
    sidecars (tests); the model directory is verified either way.
    """
    model_dir = resolve_model_dir(env, expected_model_dir)
    model_sha = model_dir_sha256(model_dir)
    overlay = load_overlay(overlay_path or os.path.join(draft_dir, "cu_stance_label_set.csv"))
    bodies = {turn_key(row): row.get("text") or row.get("body") or "" for row in fulltext_rows}
    cache = load_cache(cache_path)

    plan = []
    for row in tone_rows:
        text, body, fallback = resolve_text(row, bodies)
        plan.append((row, text, body, fallback, ("#".join(turn_key(row)), sha256_text(text), model_sha)))
    todo, queued = [], set()
    for _row, text, _body, _fallback, key in plan:
        if key not in cache and key not in queued and len(todo) < batch_limit:
            queued.add(key)
            todo.append((key, text))
    if todo:
        items = [("%d" % index, text) for index, (_key, text) in enumerate(todo)]
        debate = (debate_fn or (lambda batch, directory: run_debate(batch, directory, draft_dir=draft_dir)))(items, model_dir)
        lsd = (lsd_fn or (lambda batch: run_lsd(batch, lsd_dir=draft_dir)))(items)
        fresh = []
        for (key, _text), (item_id, _item_text) in zip(todo, items):
            if item_id not in debate or item_id not in lsd:
                raise Stage5Failed("Stage 5 returned no answer for batch item %s" % item_id)
            record = {"k": key[0], "t": key[1], "m": key[2],
                      "debate": {name: debate[item_id][name] for name in DEBATE_FIELDS},
                      "lsd": {name: lsd[item_id][name] for name in LSD_FIELDS}}
            cache[key] = record
            fresh.append(record)
        append_cache(cache_path, fresh)

    rows, scored, cached, fallbacks, overlaid = [], 0, 0, 0, 0
    for row, _text, body, fallback, key in plan:
        record = cache.get(key)
        if record is None:
            continue                                            # beyond this invocation's bound: withheld, not mislabelled
        if key in queued:
            scored += 1
        else:
            cached += 1
        has_text = len(body) >= MIN_TEXT_CHARS                  # run_stage5.R:136 (a title-only row never has text)
        final = finalize(row, record["debate"], record["lsd"], overlay.get(row.get("url") or ""), has_text)
        final["body_fallback"] = "title" if fallback or row.get("body_fallback") == "title" else ""
        fallbacks += final["body_fallback"] == "title"
        overlaid += final["stance_src"] == OVERLAY_SRC
        rows.append(final)
    report = {"scorer": STANCE_SRC, "model_dir": model_dir.as_posix(), "model_dir_sha256": model_sha,
              "rows_input": len(plan), "rows_scored": scored, "rows_cached": cached,
              "rows_pending": len(plan) - len(rows), "batch_limit": batch_limit,
              "title_fallback_rows": fallbacks, "human_overlay_rows": overlaid,
              "priority": "below_normal"}
    return rows, report


def make_scorer(**options):
    """A ``(tone_rows, fulltext_rows) -> (rows, report)`` callable for build_press_engine_feed."""
    return lambda tone_rows, fulltext_rows: apply(tone_rows, fulltext_rows, **options)
