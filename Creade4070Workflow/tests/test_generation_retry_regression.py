"""Regression tests for production error: create_retry_generation keyword args.

Production error: create_retry_generation() got an unexpected keyword argument 'source_generation_id'
"""
import pytest
from generation import (
    GenerationRecord,
    GenerationReason,
    create_generation,
    create_retry_generation,
    create_reroll_generation,
    create_regenerate_generation,
)


class TestProductionErrorRegression:
    """Reproduce and verify fix for production TypeError."""

    def test_retry_with_source_generation_id_no_error(self):
        """Production call pattern must not raise TypeError."""
        original = create_generation(
            reason=GenerationReason.INITIAL,
            source_task_id="task-001",
            source_batch_id="batch-001",
        )
        # This is how batch_executor.py calls it
        retry = create_retry_generation(
            source_generation_id=original.generation_id,
            source_variation_seed=original.variation_seed,
            variation_index=original.variation_index,
            generation_reason="system_retry",
            source_task_id=original.source_task_id,
            source_batch_id=original.source_batch_id,
        )
        assert retry.generation_id == original.generation_id
        assert retry.variation_seed == original.variation_seed
        assert retry.source_generation_id == original.generation_id

    def test_retry_with_generation_record_no_error(self):
        """Original call pattern (passing GenerationRecord) must still work."""
        original = create_generation(
            reason=GenerationReason.INITIAL,
            source_task_id="task-001",
        )
        retry = create_retry_generation(original)
        assert retry.generation_id == original.generation_id
        assert retry.variation_seed == original.variation_seed

    def test_retry_preserves_seed(self):
        """Retry must keep the same variation_seed."""
        original = create_generation(reason=GenerationReason.INITIAL)
        retry = create_retry_generation(original)
        assert retry.variation_seed == original.variation_seed

    def test_regenerate_creates_new_seed_and_saves_source(self):
        """Regenerate must create new seed and record source_generation_id."""
        original = create_generation(
            reason=GenerationReason.INITIAL,
            source_task_id="task-001",
        )
        regen = create_regenerate_generation(original)
        assert regen.generation_id != original.generation_id
        assert regen.variation_seed != original.variation_seed
        assert regen.source_generation_id == original.generation_id
        assert regen.generation_reason == "user_regenerate"
        assert regen.variation_index == original.variation_index + 1

    def test_historical_task_without_source_generation_id(self):
        """Old tasks without source_generation_id must work (defaults to None)."""
        gen = create_generation(reason=GenerationReason.INITIAL)
        assert gen.source_generation_id is None
        # to_dict must include the field
        d = gen.to_dict()
        assert "source_generation_id" in d
        assert d["source_generation_id"] is None

    def test_reroll_sets_source_generation_id(self):
        """Reroll must record the original generation as source."""
        original = create_generation(reason=GenerationReason.INITIAL)
        reroll = create_reroll_generation(original)
        assert reroll.source_generation_id == original.generation_id
        assert reroll.generation_id != original.generation_id
        assert reroll.variation_seed != original.variation_seed
        assert reroll.generation_reason == "duplicate_reroll"
        assert reroll.reroll_count == 1

    def test_production_error_replay(self):
        """Exact replay of production error must not raise."""
        # Simulate what batch_executor._execute_claimed_task does on retry
        existing_output_data = {
            "generation_id": "abc-123",
            "variation_seed": 42,
            "variation_index": 0,
            "generation_reason": "initial",
            "source_task_id": "task-001",
            "source_batch_id": "batch-001",
        }
        # This is the exact call pattern from batch_executor.py line 974
        retry = create_retry_generation(
            source_generation_id=existing_output_data.get("generation_id"),
            source_variation_seed=existing_output_data.get("variation_seed", 0),
            variation_index=existing_output_data.get("variation_index", 0),
            generation_reason="system_retry",
            source_task_id=existing_output_data.get("source_task_id"),
            source_batch_id=existing_output_data.get("source_batch_id"),
        )
        assert retry.generation_id == "abc-123"
        assert retry.variation_seed == 42

    def test_fallback_worker_create_retry(self):
        """Fallback worker calling create_retry_generation must work."""
        original = create_generation(
            reason=GenerationReason.NEW_BATCH,
            source_task_id="task-fallback",
            source_batch_id="batch-fallback",
        )
        # Fallback worker pattern
        retry = create_retry_generation(
            source_generation_id=original.generation_id,
            source_variation_seed=original.variation_seed,
            variation_index=original.variation_index,
            generation_reason="system_retry",
            source_task_id=original.source_task_id,
            source_batch_id=original.source_batch_id,
        )
        assert retry.generation_id == original.generation_id
        assert retry.variation_seed == original.variation_seed

    def test_all_generation_fields_in_to_dict(self):
        """to_dict must include all fields including source_generation_id."""
        gen = create_generation(
            reason=GenerationReason.INITIAL,
            source_task_id="task-001",
            source_batch_id="batch-001",
        )
        d = gen.to_dict()
        required_fields = [
            "generation_id", "variation_seed", "variation_index",
            "generation_reason", "source_task_id", "source_batch_id",
            "source_generation_id", "material_pool_version",
            "material_sequence_hash", "timeline_hash",
            "segment_signature_hash", "reroll_count",
        ]
        for field in required_fields:
            assert field in d, f"Missing field: {field}"
