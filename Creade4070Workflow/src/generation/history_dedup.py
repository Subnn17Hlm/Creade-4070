"""
History deduplication and reroll logic for material variation.

When a new generation produces the same material_sequence_hash or timeline_hash
as a previous successful generation for the same script, this module triggers
a reroll with a new variation_seed to produce different material selection.
"""
import hashlib
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_REROLL_ATTEMPTS = 3


def compute_normalized_script_hash(script_text: str) -> str:
    """Compute a stable hash of normalized script text for dedup lookup."""
    normalized = script_text.strip().lower()
    # Remove extra whitespace
    normalized = " ".join(normalized.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def check_history_duplication(
    normalized_script_hash: str,
    material_sequence_hash: str,
    timeline_hash: str,
    current_generation_id: str,
    historical_results: List[Dict[str, Any]],
) -> Tuple[bool, Optional[str]]:
    """
    Check if the current generation's hashes match any historical result.
    
    Args:
        normalized_script_hash: Hash of the normalized script text
        material_sequence_hash: Hash of the material sequence
        timeline_hash: Hash of the full timeline
        current_generation_id: Current generation ID to exclude from comparison
        historical_results: List of historical task results with generation info
    
    Returns:
        Tuple of (is_duplicate, reason) where reason explains which hash matched
    """
    for hist in historical_results:
        # Skip current generation
        if hist.get("generation_id") == current_generation_id:
            continue
        
        # Only compare with successful or warning results
        status = hist.get("status", "")
        if status not in ("success", "warning"):
            continue
        
        # Only compare with same script
        if hist.get("normalized_script_hash") != normalized_script_hash:
            continue
        
        # Check material_sequence_hash match
        if hist.get("material_sequence_hash") == material_sequence_hash:
            return True, f"material_sequence_hash matches generation {hist.get('generation_id', 'unknown')[:8]}"
        
        # Check timeline_hash match
        if hist.get("timeline_hash") == timeline_hash:
            return True, f"timeline_hash matches generation {hist.get('generation_id', 'unknown')[:8]}"
    
    return False, None


def create_reroll_seed(current_seed: int) -> int:
    """Create a new variation_seed for reroll, derived from current seed."""
    import secrets
    # Combine current seed with fresh randomness
    combined = hashlib.sha256(
        f"{current_seed}:{secrets.randbits(64)}".encode()
    ).hexdigest()
    return int(combined[:16], 16)


async def get_historical_results_for_script(
    db_session,
    normalized_script_hash: str,
    exclude_task_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Query historical successful/warning results for the same script.
    
    Args:
        db_session: Database session
        normalized_script_hash: Hash of the normalized script text
        exclude_task_id: Task ID to exclude (current task)
        limit: Maximum number of results to return
    
    Returns:
        List of historical results with generation info
    """
    from storage.database.batch_models import BatchTask, BatchTaskStatus
    from sqlalchemy import select
    
    query = (
        select(BatchTask)
        .where(
            BatchTask.status.in_([BatchTaskStatus.SUCCESS.value, BatchTaskStatus.WARNING.value]),
        )
        .order_by(BatchTask.completed_at.desc())
        .limit(limit)
    )
    
    result = await db_session.execute(query)
    tasks = result.scalars().all()
    
    historical = []
    for task in tasks:
        if exclude_task_id and str(task.task_id) == exclude_task_id:
            continue
        
        output_data = task.output_data or {}
        
        # Check if this task has the same script hash
        task_script_hash = output_data.get("normalized_script_hash")
        if task_script_hash != normalized_script_hash:
            continue
        
        historical.append({
            "task_id": str(task.task_id),
            "generation_id": output_data.get("generation_id"),
            "variation_seed": output_data.get("variation_seed"),
            "material_sequence_hash": output_data.get("material_sequence_hash"),
            "timeline_hash": output_data.get("timeline_hash"),
            "segment_signature_hash": output_data.get("segment_signature_hash"),
            "normalized_script_hash": task_script_hash,
            "status": task.status,
            "final_video_url": task.final_video_url,
        })
    
    return historical


async def persist_timeline_after_dedup(
    db_session,
    task_id: str,
    run_id: str,
    generation_info: Dict[str, Any],
    timeline_data: Dict[str, Any],
) -> bool:
    """
    Persist the final timeline and generation info after dedup check passes.
    This ensures that retry will reuse this exact timeline.
    
    Args:
        db_session: Database session
        task_id: Task ID
        run_id: Run ID (lease)
        generation_info: Generation snapshot with hashes
        timeline_data: Final timeline data with material selections
    
    Returns:
        True if persisted successfully
    """
    from storage.database.batch_models import BatchTask
    from sqlalchemy import select, update
    
    # Read current output_data
    result = await db_session.execute(
        select(BatchTask.output_data).where(
            BatchTask.task_id == task_id,
            BatchTask.run_id == run_id,
        )
    )
    row = result.first()
    if not row:
        return False
    
    current_output = dict(row.output_data or {})
    
    # Merge generation info and timeline data
    current_output.update(generation_info)
    current_output["final_timeline"] = timeline_data
    current_output["timeline_persisted"] = True
    
    # Atomic update
    update_result = await db_session.execute(
        update(BatchTask)
        .where(
            BatchTask.task_id == task_id,
            BatchTask.run_id == run_id,
        )
        .values(output_data=current_output)
    )
    await db_session.commit()
    
    return update_result.rowcount == 1


async def restore_persisted_timeline(
    db_session,
    task_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Restore a previously persisted timeline for retry.
    
    Args:
        db_session: Database session
        task_id: Task ID
    
    Returns:
        Persisted timeline data if available, None otherwise
    """
    from storage.database.batch_models import BatchTask
    from sqlalchemy import select
    
    result = await db_session.execute(
        select(BatchTask.output_data).where(BatchTask.task_id == task_id)
    )
    row = result.first()
    if not row:
        return None
    
    output_data = row.output_data or {}
    if output_data.get("timeline_persisted") and output_data.get("final_timeline"):
        return {
            "generation_id": output_data.get("generation_id"),
            "variation_seed": output_data.get("variation_seed"),
            "variation_index": output_data.get("variation_index"),
            "generation_reason": output_data.get("generation_reason"),
            "material_sequence_hash": output_data.get("material_sequence_hash"),
            "timeline_hash": output_data.get("timeline_hash"),
            "segment_signature_hash": output_data.get("segment_signature_hash"),
            "final_timeline": output_data.get("final_timeline"),
            "reroll_count": output_data.get("reroll_count", 0),
        }
    
    return None
