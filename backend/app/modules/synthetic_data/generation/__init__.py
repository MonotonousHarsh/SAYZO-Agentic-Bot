"""AI-assisted dataset generation.

An LLM writes realistic message, page and meeting *text*. Deterministic code decides
everything structural: ids, authors, timestamps, channels and access groups. Every result is
filtered, then the whole dataset must pass the same validator as the hand-written seed.
"""

from app.modules.synthetic_data.generation.generator import (
    DatasetAugmenter,
    GenerationResult,
    GenerationSpec,
)

__all__ = ["DatasetAugmenter", "GenerationResult", "GenerationSpec"]
