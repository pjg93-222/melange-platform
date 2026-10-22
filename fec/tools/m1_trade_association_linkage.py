# -*- coding: utf-8 -*-
"""Flag-gated trade-association candidate linkage review tooling."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import math
import os
from pathlib import Path
import sys
import tempfile
import unicodedata
from typing import Callable, Iterable, Sequence


SCHEMA_VERSION = "m1-trade-association-review-v1"
QUERY_COLUMNS = ("source_record_id", "source_name")
CANDIDATE_COLUMNS = ("candidate_id", "candidate_name")
REVIEW_COLUMNS = (
    "schema_version",
    "source_record_id",
    "source_name",
    "source_name_normalized",
    "candidate_rank",
    "candidate_id",
    "candidate_name",
    "candidate_name_normalized",
    "similarity_backend",
    "similarity_score",
    "score_margin_to_next",
    "queue_reason",
    "review_decision",
    "reviewed_candidate_id",
    "reviewer",
    "review_notes",
    "queries_sha256",
    "candidates_sha256",
)
DEFAULT_BACKEND_NAME = "sklearn-char_wb-3-5-tfidf"
QUEUE_REASONS = {
    "NORMALIZED_EXACT",
    "FUZZY_CANDIDATE",
    "BELOW_REVIEW_FLOOR",
}


class LinkageError(ValueError):
    """A closed-gate input or output validation failure."""


def normalize_name(value: str) -> str:
    """Return the approved Unicode/case/punctuation name normalization."""
    text = unicodedata.normalize("NFKC", str(value)).casefold().replace("&", " and ")
    text = "".join(
        character
        for character in text
        if unicodedata.category(character) != "Cf"
    )
    text = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in text
    )
    return " ".join(text.split())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_csv_input(path: Path, label: str) -> None:
    if path.suffix.casefold() != ".csv":
        raise LinkageError(f"{label} must be a .csv file")
    if not path.is_file():
        raise LinkageError(f"{label} does not exist: {path}")


def _load_csv(path: Path, required: Sequence[str], id_field: str, name_field: str) -> list[dict[str, str]]:
    _validate_csv_input(path, path.name)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames
            if headers is None or any(column not in headers for column in required):
                raise LinkageError(
                    f"{path.name} requires columns: {', '.join(required)}"
                )
            rows: list[dict[str, str]] = []
            identifiers: set[str] = set()
            for line_number, raw in enumerate(reader, start=2):
                identifier = str(raw.get(id_field) or "").strip()
                name = str(raw.get(name_field) or "").strip()
                if not identifier or not name:
                    raise LinkageError(
                        f"{path.name} line {line_number} has a blank ID or name"
                    )
                if identifier in identifiers:
                    raise LinkageError(
                        f"{path.name} line {line_number} repeats ID {identifier!r}"
                    )
                normalized = normalize_name(name)
                if not normalized:
                    raise LinkageError(
                        f"{path.name} line {line_number} has an empty normalized name"
                    )
                identifiers.add(identifier)
                rows.append(
                    {
                        id_field: identifier,
                        name_field: name,
                        f"{name_field}_normalized": normalized,
                    }
                )
    except UnicodeError as exc:
        raise LinkageError(f"{path.name} is not valid UTF-8") from exc
    return rows


def load_queries(path: Path) -> tuple[list[dict[str, str]], str]:
    rows = _load_csv(path, QUERY_COLUMNS, "source_record_id", "source_name")
    return rows, sha256_file(path)


def load_candidates(path: Path) -> tuple[list[dict[str, str]], str]:
    rows = _load_csv(path, CANDIDATE_COLUMNS, "candidate_id", "candidate_name")
    if not rows:
        raise LinkageError("candidate catalog must not be empty")
    return rows, sha256_file(path)


class CharNgramTfidfBackend:
    """The fixed sklearn character n-gram TF-IDF similarity backend."""

    name = DEFAULT_BACKEND_NAME

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            lowercase=False,
            norm="l2",
        )

    def __call__(
        self,
        normalized_queries: list[str],
        normalized_candidates: list[str],
    ) -> list[list[float]]:
        import numpy

        candidate_matrix = self.vectorizer.fit_transform(normalized_candidates)
        query_matrix = self.vectorizer.transform(normalized_queries)
        # L2-normalised dot products can round to 1.0000000000000002; clamp round-off.
        return numpy.clip((query_matrix @ candidate_matrix.T).toarray(), 0.0, 1.0).tolist()


def load_plugin(specification: str) -> Callable[[list[str], list[str]], object]:
    if specification.count(":") != 1:
        raise LinkageError("backend plug-in must have module:function form")
    module_name, function_name = specification.split(":", 1)
    if not module_name or not function_name:
        raise LinkageError("backend plug-in must have module:function form")
    try:
        module = importlib.import_module(module_name)
        backend = getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        raise LinkageError("backend plug-in could not be loaded") from exc
    if not callable(backend):
        raise LinkageError("backend plug-in target is not callable")
    return backend


def validate_score_matrix(
    raw_scores: object,
    query_count: int,
    candidate_count: int,
) -> list[list[float]]:
    try:
        outer = list(raw_scores)  # type: ignore[arg-type]
    except TypeError as exc:
        raise LinkageError("backend scores must be a two-dimensional sequence") from exc
    if len(outer) != query_count:
        raise LinkageError("backend score row count does not match queries")
    scores: list[list[float]] = []
    for raw_row in outer:
        try:
            row = [float(value) for value in raw_row]
        except (TypeError, ValueError) as exc:
            raise LinkageError("backend scores must be numeric rows") from exc
        if len(row) != candidate_count:
            raise LinkageError("backend score column count does not match candidates")
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in row):
            raise LinkageError("backend scores must be finite values in [0,1]")
        scores.append(row)
    return scores


def score_queries(
    queries: Sequence[dict[str, str]],
    candidates: Sequence[dict[str, str]],
    plugin_specification: str | None = None,
) -> tuple[str, list[list[float]]]:
    normalized_queries = [row["source_name_normalized"] for row in queries]
    normalized_candidates = [row["candidate_name_normalized"] for row in candidates]
    if plugin_specification is None:
        backend: Callable[[list[str], list[str]], object] = CharNgramTfidfBackend()
        backend_name = DEFAULT_BACKEND_NAME
    else:
        backend = load_plugin(plugin_specification)
        backend_name = f"plugin:{plugin_specification}"
    raw_scores = backend(normalized_queries, normalized_candidates)
    return backend_name, validate_score_matrix(
        raw_scores, len(queries), len(candidates)
    )


def _format_score(value: float) -> str:
    if abs(value) < 0.000000005:
        value = 0.0
    return f"{value:.8f}"


def build_review_rows(
    queries: Sequence[dict[str, str]],
    candidates: Sequence[dict[str, str]],
    scores: Sequence[Sequence[float]],
    backend_name: str,
    top_k: int,
    review_floor: float,
    queries_sha256: str,
    candidates_sha256: str,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for query_index, query in enumerate(queries):
        ranked = sorted(
            range(len(candidates)),
            key=lambda index: (
                -scores[query_index][index],
                candidates[index]["candidate_name_normalized"],
                candidates[index]["candidate_id"],
            ),
        )
        for offset, candidate_index in enumerate(ranked[:top_k]):
            candidate = candidates[candidate_index]
            if (
                query["source_name_normalized"]
                == candidate["candidate_name_normalized"]
            ):
                reason = "NORMALIZED_EXACT"
            elif scores[query_index][candidate_index] < review_floor:
                reason = "BELOW_REVIEW_FLOOR"
            else:
                reason = "FUZZY_CANDIDATE"
            next_offset = offset + 1
            margin = ""
            if next_offset < len(ranked):
                next_index = ranked[next_offset]
                margin = _format_score(
                    scores[query_index][candidate_index]
                    - scores[query_index][next_index]
                )
            output.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "source_record_id": query["source_record_id"],
                    "source_name": query["source_name"],
                    "source_name_normalized": query["source_name_normalized"],
                    "candidate_rank": str(offset + 1),
                    "candidate_id": candidate["candidate_id"],
                    "candidate_name": candidate["candidate_name"],
                    "candidate_name_normalized": candidate[
                        "candidate_name_normalized"
                    ],
                    "similarity_backend": backend_name,
                    "similarity_score": _format_score(
                        scores[query_index][candidate_index]
                    ),
                    "score_margin_to_next": margin,
                    "queue_reason": reason,
                    "review_decision": "",
                    "reviewed_candidate_id": "",
                    "reviewer": "",
                    "review_notes": "",
                    "queries_sha256": queries_sha256,
                    "candidates_sha256": candidates_sha256,
                }
            )
    return output


def validate_output_path(output: Path, queries: Path, candidates: Path) -> None:
    if output.suffix.casefold() != ".csv":
        raise LinkageError("review output must end in .csv")
    if not output.parent.is_dir():
        raise LinkageError("review output parent directory does not exist")
    output_resolved = output.resolve()
    if output_resolved in {queries.resolve(), candidates.resolve()}:
        raise LinkageError("review output must differ from both inputs")


def write_review_csv(path: Path, rows: Iterable[dict[str, str]]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--emit-review", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--review-floor", type=float, default=0.35)
    parser.add_argument("--backend-plugin")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.top_k <= 50:
            raise LinkageError("--top-k must be in the range 1..50")
        if not math.isfinite(args.review_floor) or not 0.0 <= args.review_floor <= 1.0:
            raise LinkageError("--review-floor must be a finite value in [0,1]")
        _validate_csv_input(args.queries, "queries")
        _validate_csv_input(args.candidates, "candidates")
        if args.emit_review:
            if args.output is None:
                raise LinkageError("--output is required with --emit-review")
            validate_output_path(args.output, args.queries, args.candidates)
        queries, queries_hash = load_queries(args.queries)
        candidates, candidates_hash = load_candidates(args.candidates)
        backend_name, scores = score_queries(
            queries, candidates, args.backend_plugin
        )
        rows = build_review_rows(
            queries,
            candidates,
            scores,
            backend_name,
            args.top_k,
            args.review_floor,
            queries_hash,
            candidates_hash,
        )
        if args.emit_review:
            write_review_csv(args.output, rows)
        print(
            f"queries={len(queries)} candidates={len(candidates)} "
            f"review_rows={len(rows)} emitted={int(args.emit_review)}"
        )
        return 0
    except (LinkageError, OSError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
