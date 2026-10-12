# -*- coding: utf-8 -*-
"""Command-line entry point for canonical facts and typed network generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .canonical_facts import build_canonical_generation
from .graph_build import build_network_generation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p2-root", required=True)
    parser.add_argument("--lda-root", required=True)
    parser.add_argument("--politico-root", required=True)
    parser.add_argument("--identity-candidates-root", required=True)
    parser.add_argument("--legislators-current", required=True)
    parser.add_argument("--analysis-authority-manifest", required=True)
    parser.add_argument("--canonical-output", required=True)
    parser.add_argument("--network-output", required=True)
    parser.add_argument("--generated-at")
    parser.add_argument(
        "--exclude-identity-candidates", action="store_true",
        help="Exclude even unambiguous exact-name review candidates from the exploratory graph layer.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    canonical = build_canonical_generation(
        p2_root=args.p2_root,
        lda_root=args.lda_root,
        politico_root=args.politico_root,
        identity_candidates_root=args.identity_candidates_root,
        legislators_current_path=args.legislators_current,
        analysis_authority_manifest_path=args.analysis_authority_manifest,
        output_root=args.canonical_output,
        generated_at=args.generated_at,
    )
    network = build_network_generation(
        canonical_root=args.canonical_output,
        output_root=args.network_output,
        generated_at=args.generated_at,
        include_unambiguous_identity_candidates=not args.exclude_identity_candidates,
    )
    print(
        json.dumps(
            {
                "canonical_output": str(Path(args.canonical_output).resolve()),
                "canonical_receipt": canonical,
                "network_output": str(Path(args.network_output).resolve()),
                "network_receipt": network,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
