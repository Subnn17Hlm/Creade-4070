"""Generation version model for tracking material selection variations."""
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any


class GenerationReason(str, Enum):
    INITIAL = "initial"
    SYSTEM_RETRY = "system_retry"
    NEW_BATCH = "new_batch"
    USER_REGENERATE = "user_regenerate"
    DUPLICATE_REROLL = "duplicate_reroll"


@dataclass
class GenerationRecord:
    """Tracks a single generation version for a task."""
    generation_id: str
    variation_seed: int
    variation_index: int
    generation_reason: str
    source_task_id: Optional[str] = None
    source_batch_id: Optional[str] = None
    timeline_hash: str = ""
    material_sequence_hash: str = ""
    segment_signature_hash: str = ""
    material_pool_version: str = ""
    created_at: str = ""
    reroll_count: int = 0
    reroll_history: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GenerationRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def create_generation(
    reason: GenerationReason = GenerationReason.INITIAL,
    source_task_id: Optional[str] = None,
    source_batch_id: Optional[str] = None,
    variation_index: int = 0,
    material_pool_version: str = "",
) -> GenerationRecord:
    """Create a new generation record with a fresh random seed."""
    return GenerationRecord(
        generation_id=str(uuid.uuid4()),
        variation_seed=secrets.randbits(63),
        variation_index=variation_index,
        generation_reason=reason.value,
        source_task_id=source_task_id,
        source_batch_id=source_batch_id,
        material_pool_version=material_pool_version,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def create_retry_generation(original: GenerationRecord) -> GenerationRecord:
    """Create a retry generation that reuses the original seed for idempotency."""
    return GenerationRecord(
        generation_id=original.generation_id,
        variation_seed=original.variation_seed,
        variation_index=original.variation_index,
        generation_reason=GenerationReason.SYSTEM_RETRY.value,
        source_task_id=original.source_task_id,
        source_batch_id=original.source_batch_id,
        timeline_hash=original.timeline_hash,
        material_sequence_hash=original.material_sequence_hash,
        segment_signature_hash=original.segment_signature_hash,
        material_pool_version=original.material_pool_version,
        created_at=datetime.now(timezone.utc).isoformat(),
        reroll_count=original.reroll_count,
        reroll_history=list(original.reroll_history),
        warnings=list(original.warnings),
    )


def create_reroll_generation(
    original: GenerationRecord,
    reason: GenerationReason = GenerationReason.DUPLICATE_REROLL,
) -> GenerationRecord:
    """Create a reroll generation with a new seed to produce different results."""
    return GenerationRecord(
        generation_id=str(uuid.uuid4()),
        variation_seed=secrets.randbits(63),
        variation_index=original.variation_index + 1,
        generation_reason=reason.value,
        source_task_id=original.source_task_id,
        source_batch_id=original.source_batch_id,
        material_pool_version=original.material_pool_version,
        created_at=datetime.now(timezone.utc).isoformat(),
        reroll_count=original.reroll_count + 1,
        reroll_history=original.reroll_history + [{
            "old_generation_id": original.generation_id,
            "old_material_sequence_hash": original.material_sequence_hash,
            "old_timeline_hash": original.timeline_hash,
            "reason": reason.value,
        }],
    )
