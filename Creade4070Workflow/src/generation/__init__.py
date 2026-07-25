"""Generation version model for material selection variation."""
from generation.generation_model import (
    GenerationRecord,
    GenerationReason,
    create_generation,
    create_retry_generation,
    create_reroll_generation,
    create_regenerate_generation,
)
from generation.variation import VariationRNG
from generation.hash_utils import (
    compute_material_sequence_hash,
    compute_timeline_hash,
    compute_segment_signature_hash,
    compute_material_pool_version,
)
from generation.history_dedup import (
    compute_normalized_script_hash,
    check_history_duplication,
    create_reroll_seed,
    get_historical_results_for_script,
    persist_timeline_after_dedup,
    restore_persisted_timeline,
    MAX_REROLL_ATTEMPTS,
)

__all__ = [
    "GenerationRecord",
    "GenerationReason",
    "create_generation",
    "create_retry_generation",
    "create_reroll_generation",
    "create_regenerate_generation",
    "VariationRNG",
    "compute_material_sequence_hash",
    "compute_timeline_hash",
    "compute_segment_signature_hash",
    "compute_material_pool_version",
    "compute_normalized_script_hash",
    "check_history_duplication",
    "create_reroll_seed",
    "get_historical_results_for_script",
    "persist_timeline_after_dedup",
    "restore_persisted_timeline",
    "MAX_REROLL_ATTEMPTS",
]
