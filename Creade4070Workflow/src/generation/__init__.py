"""Generation version model for material selection variation."""
from generation.generation_model import (
    GenerationRecord,
    GenerationReason,
    create_generation,
    create_retry_generation,
    create_reroll_generation,
)
from generation.variation import VariationRNG
from generation.hash_utils import (
    compute_material_sequence_hash,
    compute_timeline_hash,
    compute_segment_signature_hash,
    compute_material_pool_version,
)

__all__ = [
    "GenerationRecord",
    "GenerationReason",
    "create_generation",
    "create_retry_generation",
    "create_reroll_generation",
    "VariationRNG",
    "compute_material_sequence_hash",
    "compute_timeline_hash",
    "compute_segment_signature_hash",
    "compute_material_pool_version",
]
