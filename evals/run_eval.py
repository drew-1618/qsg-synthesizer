"""Measure how well an extractor reads the sample and fixture documents.

    python -m evals.run_eval --provider heuristic
    python -m evals.run_eval --provider gemini --model gemini-2.5-flash
    python -m evals.run_eval --provider anthropic --only router

Each document in `evals/expected.json` is read the way an upload is
(`document_parser.extract_text`) and extracted the way the app does it
(`orchestrator._extract_document`, so long documents are chunked and a
failed LLM call falls back to the built-in parser). A fallback is reported
and left out of the provider's totals rather than scored as its reading.

An AI provider uses the key in ANTHROPIC_API_KEY / GEMINI_API_KEY, from the
environment or ./.env, and is only called when named with --provider: each
run makes one or more real API calls per document.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from evals.scoring import FIELDS, score_extraction
from qsg_synthesizer.services import orchestrator
from qsg_synthesizer.tools.document_parser import extract_text

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PATH = Path(__file__).resolve().parent / "expected.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
PROVIDERS = ("heuristic", "anthropic", "gemini")

# Short column names for the report.
_COLUMNS = {
    "device_name": "Name",
    "document_type": "Type",
    "default_ip": "IP",
    "provisioning_methods": "Prov.",
    "required_tools": "Tools",
    "ports": "Ports",
    "leds": "LEDs",
    "wifi_standard": "Wi-Fi",
    "wan_speed": "WAN",
    "specs": "Specs",
}


@dataclass
class DocumentResult:
    document: str
    read_with: str
    seconds: float
    scores: dict[str, float] = field(default_factory=dict)
    # Set when an AI provider was asked for but the built-in parser read it.
    fell_back: bool = False
    warnings: list[str] = field(default_factory=list)


def load_expected() -> dict[str, dict]:
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))["documents"]


def run(
    provider: str, model: Optional[str] = None, only: Optional[str] = None
) -> list[DocumentResult]:
    llm = orchestrator.LLMOverride(provider=provider, model=model)  # type: ignore[arg-type]
    results = []
    for document, expected in load_expected().items():
        if only and only not in document:
            continue
        path = ROOT / document
        text = extract_text(path.name, path.read_bytes())
        started = time.perf_counter()
        extraction, source, warnings = orchestrator._extract_document(
            text, path.name, llm
        )
        seconds = time.perf_counter() - started
        results.append(
            DocumentResult(
                document=document,
                read_with=orchestrator._extracted_with(source, llm),
                seconds=round(seconds, 2),
                scores=score_extraction(extraction, expected),
                fell_back=provider != "heuristic"
                and source == orchestrator.EXTRACTION_SOURCE_HEURISTIC,
                warnings=warnings,
            )
        )
    return results


def field_means(results: list[DocumentResult]) -> dict[str, float]:
    """Each field's mean score over the documents that score it, leaving out
    documents an AI provider didn't actually read."""
    means = {}
    for name in FIELDS:
        values = [
            r.scores[name] for r in results if name in r.scores and not r.fell_back
        ]
        if values:
            means[name] = sum(values) / len(values)
    return means


def overall(results: list[DocumentResult]) -> Optional[float]:
    values = [v for r in results if not r.fell_back for v in r.scores.values()]
    return sum(values) / len(values) if values else None


def report(results: list[DocumentResult]) -> str:
    """A Markdown table: one row per document, then the per-field means."""
    columns = [name for name in FIELDS if any(name in r.scores for r in results)]
    header = ["Document", *(_COLUMNS[name] for name in columns), "Seconds"]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]

    def cell(value: Optional[float]) -> str:
        return "" if value is None else f"{value:.2f}"

    for r in results:
        name = Path(r.document).name + (" (fell back)" if r.fell_back else "")
        cells = [cell(r.scores.get(c)) for c in columns]
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {r.seconds:.1f} |")
    means = field_means(results)
    seconds = sum(r.seconds for r in results)
    lines.append(
        "| **Mean** | "
        + " | ".join(f"**{cell(means.get(c))}**" for c in columns)
        + f" | {seconds:.1f} |"
    )
    total = overall(results)
    summary = [
        "",
        f"Read with: {', '.join(sorted({r.read_with for r in results}))}",
        f"Overall: {cell(total) or 'n/a'} over {len(results)} documents",
    ]
    fell_back = [Path(r.document).name for r in results if r.fell_back]
    if fell_back:
        summary.append(
            f"Fell back to the built-in parser (left out of the means): "
            f"{', '.join(fell_back)}"
        )
    return "\n".join(lines + summary)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    parser.add_argument("--model", help="One of the provider's LLM_MODEL_CHOICES")
    parser.add_argument("--only", help="Only documents whose path contains this")
    parser.add_argument(
        "--no-save", action="store_true", help="Don't write evals/results/*.json"
    )
    args = parser.parse_args(argv)

    if args.provider != "heuristic":
        # Never under pytest, so tests can't pick up real keys (as in main.py).
        if "PYTEST_VERSION" not in os.environ:
            from dotenv import load_dotenv

            load_dotenv(Path.cwd() / ".env")
        if not orchestrator._provider_ready(args.provider):
            print(
                f"No usable {args.provider} key or package: set "
                f"{args.provider.upper()}_API_KEY (environment or ./.env).",
                file=sys.stderr,
            )
            return 2
        choices = [model for model, _ in orchestrator.LLM_MODEL_CHOICES[args.provider]]
        if args.model and args.model not in choices:
            print(f"--model must be one of: {', '.join(choices)}", file=sys.stderr)
            return 2

    results = run(args.provider, args.model, args.only)
    if not results:
        print("No documents matched --only.", file=sys.stderr)
        return 2
    print(report(results))
    if not args.no_save:
        RESULTS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        label = args.provider + (f"-{args.model}" if args.model else "")
        out = RESULTS_DIR / f"{stamp}-{label}.json"
        out.write_text(
            json.dumps(
                {
                    "provider": args.provider,
                    "model": args.model,
                    "means": field_means(results),
                    "overall": overall(results),
                    "documents": [asdict(r) for r in results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
