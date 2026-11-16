"""Generate a larger dataset: the seed dataset plus LLM-written topics, validated end to end.

Usage (from the backend folder, with GROQ_API_KEY set in the repo-root .env):
    python scripts/generate_dataset.py
    python scripts/generate_dataset.py --topics-per-project 3 --seed 7 --name sayzo_gen_v2

The generation log (model, prompt version, raw replies, everything dropped and why) is
always written. The dataset files are written only if the result passes validation.
"""

import argparse
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.modules.synthetic_data import DatasetLoadError, DatasetValidator
from app.modules.synthetic_data.generation import DatasetAugmenter, GenerationSpec
from app.modules.synthetic_data.loader import (
    load_raw_dataset,
    to_synthetic_dataset,
    write_json,
    write_raw_dataset,
)
from app.shared.llm.groq_provider import GroqProvider

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
LOG_FILE = "generation_log.json"


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    out = args.out or DATASETS / "generated" / args.name
    if out.exists() and any(out.iterdir()) and not args.force:
        print(f"{out} already exists; pass --force to overwrite it.", file=sys.stderr)
        return 1

    settings = get_settings()
    if settings.groq_api_key is None:
        print("GROQ_API_KEY is not set (add it to the .env file).", file=sys.stderr)
        return 1

    try:
        base = load_raw_dataset(args.base)
    except DatasetLoadError as error:
        print(f"Could not load the base dataset: {error}", file=sys.stderr)
        return 1

    llm = GroqProvider(
        api_key=settings.groq_api_key.get_secret_value(),
        model=settings.groq_model,
        base_url=settings.groq_base_url,
        timeout_seconds=settings.groq_timeout_seconds,
    )
    spec = GenerationSpec(
        dataset_name=args.name,
        topics_per_project=args.topics_per_project,
        seed=args.seed,
    )
    result = DatasetAugmenter(llm, spec).augment(base)

    out.mkdir(parents=True, exist_ok=True)
    write_json(out / LOG_FILE, result.log)
    report = DatasetValidator().validate(to_synthetic_dataset(result.dataset))

    print(
        f"Topics: {len(result.log['topics'])}, records kept: {result.kept_count}, "
        f"dropped: {result.dropped_count}, failed calls: {len(result.log['failures'])}"
    )
    for issue in report.issues:
        print(f"  {issue}")
    if not report.is_valid:
        print(f"INVALID: dataset not written. See {out / LOG_FILE}.")
        return 1

    write_raw_dataset(result.dataset, out)
    print(f"VALID: dataset written to {out}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an LLM-augmented SAYZO dataset.")
    parser.add_argument("--base", type=Path, default=DATASETS / "seed")
    parser.add_argument("--name", default="sayzo_generated_v1")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--topics-per-project", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
