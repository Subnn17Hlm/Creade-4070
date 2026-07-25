"""Hash utilities for material sequence and timeline dedup."""
import hashlib
import json
from typing import List, Dict, Any


def _discretize_time(t: float, resolution: float = 0.5) -> int:
    """Discretize a time value to reduce sensitivity to tiny differences."""
    return int(round(t / resolution))


def compute_material_sequence_hash(
    material_assignments: List[Dict[str, Any]],
) -> str:
    """Compute a stable hash of the material sequence.
    
    Records material_id + discretized source_start/source_end in timeline order.
    Used to detect if the same materials are selected in the same order.
    """
    entries = []
    for m in material_assignments:
        entries.append({
            "material_id": m.get("material_id", ""),
            "source_start": _discretize_time(m.get("source_start", 0)),
            "source_end": _discretize_time(m.get("source_end", 0)),
        })
    canonical = json.dumps(entries, sort_keys=False, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_timeline_hash(
    timeline_entries: List[Dict[str, Any]],
) -> str:
    """Compute a stable hash of the full timeline.
    
    Includes material_id, source_start/end, segment_id, timeline_start/end,
    playback_rate, transition, crop_mode.
    """
    entries = []
    for e in timeline_entries:
        entries.append({
            "material_id": e.get("material_id", ""),
            "source_start": _discretize_time(e.get("source_start", 0)),
            "source_end": _discretize_time(e.get("source_end", 0)),
            "segment_id": e.get("segment_id", ""),
            "timeline_start": round(e.get("timeline_start", 0), 2),
            "timeline_end": round(e.get("timeline_end", 0), 2),
            "playback_rate": round(e.get("playback_rate", 1.0), 2),
            "transition": e.get("transition", ""),
            "crop_mode": e.get("crop_mode", ""),
        })
    canonical = json.dumps(entries, sort_keys=False, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_segment_signature_hash(
    segments: List[Dict[str, Any]],
) -> str:
    """Compute a hash for each segment's material + trim assignment.
    
    Returns a combined hash of all segment signatures.
    Used to identify which specific segments are duplicated.
    """
    signatures = []
    for seg in segments:
        sig = {
            "segment_id": seg.get("segment_id", ""),
            "segment_index": seg.get("segment_index", 0),
            "material_id": seg.get("material_id", ""),
            "source_start": _discretize_time(seg.get("source_start", 0)),
            "source_end": _discretize_time(seg.get("source_end", 0)),
        }
        signatures.append(sig)
    canonical = json.dumps(signatures, sort_keys=False, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_material_pool_version(materials: List[Dict[str, Any]]) -> str:
    """Compute a version hash for the material pool.
    
    Changes when materials are added, removed, enabled, disabled, or tags modified.
    """
    entries = []
    for m in sorted(materials, key=lambda x: x.get("asset_id", "")):
        entries.append({
            "asset_id": m.get("asset_id", ""),
            "primary_scene_tag": m.get("primary_scene_tag", ""),
            "duration_sec": round(m.get("duration_sec", 0), 2),
            "enabled": m.get("enabled", True),
        })
    canonical = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
