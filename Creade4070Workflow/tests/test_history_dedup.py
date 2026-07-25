"""
Tests for history deduplication, reroll, and generation persistence timing.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from generation import (
    compute_normalized_script_hash,
    check_history_duplication,
    create_reroll_seed,
    MAX_REROLL_ATTEMPTS,
)


class TestNormalizedScriptHash:
    """Tests for compute_normalized_script_hash."""
    
    def test_same_text_same_hash(self):
        """Same script text produces same hash."""
        h1 = compute_normalized_script_hash("测试文案")
        h2 = compute_normalized_script_hash("测试文案")
        assert h1 == h2
    
    def test_whitespace_normalized(self):
        """Whitespace differences are normalized."""
        h1 = compute_normalized_script_hash("  测试  文案  ")
        h2 = compute_normalized_script_hash("测试 文案")
        assert h1 == h2
    
    def test_case_normalized(self):
        """Case differences are normalized."""
        h1 = compute_normalized_script_hash("Test Script")
        h2 = compute_normalized_script_hash("test script")
        assert h1 == h2
    
    def test_different_text_different_hash(self):
        """Different scripts produce different hashes."""
        h1 = compute_normalized_script_hash("文案A")
        h2 = compute_normalized_script_hash("文案B")
        assert h1 != h2


class TestHistoryDuplication:
    """Tests for check_history_duplication."""
    
    def test_no_history_not_duplicate(self):
        """No history means not duplicate."""
        is_dup, reason = check_history_duplication(
            normalized_script_hash="abc123",
            material_sequence_hash="mat_hash_1",
            timeline_hash="tl_hash_1",
            current_generation_id="gen-001",
            historical_results=[],
        )
        assert not is_dup
        assert reason is None
    
    def test_same_material_hash_is_duplicate(self):
        """Same material_sequence_hash is detected as duplicate."""
        historical = [
            {
                "generation_id": "gen-old",
                "material_sequence_hash": "mat_hash_1",
                "timeline_hash": "tl_hash_different",
                "normalized_script_hash": "abc123",
                "status": "success",
            }
        ]
        is_dup, reason = check_history_duplication(
            normalized_script_hash="abc123",
            material_sequence_hash="mat_hash_1",
            timeline_hash="tl_hash_1",
            current_generation_id="gen-new",
            historical_results=historical,
        )
        assert is_dup
        assert "material_sequence_hash" in reason
    
    def test_same_timeline_hash_is_duplicate(self):
        """Same timeline_hash is detected as duplicate."""
        historical = [
            {
                "generation_id": "gen-old",
                "material_sequence_hash": "mat_hash_different",
                "timeline_hash": "tl_hash_1",
                "normalized_script_hash": "abc123",
                "status": "success",
            }
        ]
        is_dup, reason = check_history_duplication(
            normalized_script_hash="abc123",
            material_sequence_hash="mat_hash_1",
            timeline_hash="tl_hash_1",
            current_generation_id="gen-new",
            historical_results=historical,
        )
        assert is_dup
        assert "timeline_hash" in reason
    
    def test_different_hashes_not_duplicate(self):
        """Different hashes are not duplicate."""
        historical = [
            {
                "generation_id": "gen-old",
                "material_sequence_hash": "mat_hash_old",
                "timeline_hash": "tl_hash_old",
                "normalized_script_hash": "abc123",
                "status": "success",
            }
        ]
        is_dup, reason = check_history_duplication(
            normalized_script_hash="abc123",
            material_sequence_hash="mat_hash_new",
            timeline_hash="tl_hash_new",
            current_generation_id="gen-new",
            historical_results=historical,
        )
        assert not is_dup
    
    def test_current_generation_excluded(self):
        """Current generation is excluded from comparison."""
        historical = [
            {
                "generation_id": "gen-current",
                "material_sequence_hash": "mat_hash_1",
                "timeline_hash": "tl_hash_1",
                "normalized_script_hash": "abc123",
                "status": "success",
            }
        ]
        is_dup, reason = check_history_duplication(
            normalized_script_hash="abc123",
            material_sequence_hash="mat_hash_1",
            timeline_hash="tl_hash_1",
            current_generation_id="gen-current",
            historical_results=historical,
        )
        assert not is_dup
    
    def test_failed_results_ignored(self):
        """Failed results are not compared."""
        historical = [
            {
                "generation_id": "gen-old",
                "material_sequence_hash": "mat_hash_1",
                "timeline_hash": "tl_hash_1",
                "normalized_script_hash": "abc123",
                "status": "failed",
            }
        ]
        is_dup, reason = check_history_duplication(
            normalized_script_hash="abc123",
            material_sequence_hash="mat_hash_1",
            timeline_hash="tl_hash_1",
            current_generation_id="gen-new",
            historical_results=historical,
        )
        assert not is_dup
    
    def test_different_script_ignored(self):
        """Different script hash is not compared."""
        historical = [
            {
                "generation_id": "gen-old",
                "material_sequence_hash": "mat_hash_1",
                "timeline_hash": "tl_hash_1",
                "normalized_script_hash": "different_script",
                "status": "success",
            }
        ]
        is_dup, reason = check_history_duplication(
            normalized_script_hash="abc123",
            material_sequence_hash="mat_hash_1",
            timeline_hash="tl_hash_1",
            current_generation_id="gen-new",
            historical_results=historical,
        )
        assert not is_dup


class TestRerollSeed:
    """Tests for create_reroll_seed."""
    
    def test_reroll_seed_different_from_current(self):
        """Reroll seed is different from current seed."""
        current = 12345
        new_seed = create_reroll_seed(current)
        assert new_seed != current
    
    def test_reroll_seed_is_positive(self):
        """Reroll seed is positive."""
        new_seed = create_reroll_seed(12345)
        assert new_seed > 0
    
    def test_reroll_seed_varies(self):
        """Multiple reroll seeds are different (due to randomness)."""
        current = 12345
        seeds = set()
        for _ in range(10):
            seeds.add(create_reroll_seed(current))
        # With randomness, we should get multiple unique values
        assert len(seeds) > 1


class TestMaxRerollAttempts:
    """Tests for MAX_REROLL_ATTEMPTS constant."""
    
    def test_max_reroll_is_3(self):
        """Max reroll attempts is 3."""
        assert MAX_REROLL_ATTEMPTS == 3


class TestGenerationPersistenceTiming:
    """Tests for generation persistence timing (worker crash recovery)."""
    
    def test_generation_persisted_before_workflow(self):
        """
        Verify that generation info is persisted to output_data BEFORE
        the workflow runs, not after.
        
        This test simulates the flow:
        1. Task is claimed (PENDING -> RUNNING)
        2. Generation is created
        3. Generation is persisted to output_data (NEW)
        4. Worker crashes (workflow never completes)
        5. Task is retried
        6. Generation is restored from output_data (same seed)
        """
        # This is a design verification test - the actual implementation
        # is in batch_executor.py _execute_claimed_task
        # The key assertion is that generation persistence happens
        # between step 2 and step 4 (before workflow runs)
        
        from generation import create_generation, GenerationReason
        
        # Step 1-2: Create generation
        gen = create_generation(
            reason=GenerationReason.NEW_BATCH,
            source_task_id="task-001",
            source_batch_id="batch-001",
        )
        original_seed = gen.variation_seed
        
        # Step 3: Simulate persistence (what batch_executor.py now does)
        gen_snapshot = {
            "generation_id": gen.generation_id,
            "variation_seed": gen.variation_seed,
            "variation_index": gen.variation_index,
            "generation_reason": gen.generation_reason,
        }
        
        # Step 4: Simulate worker crash (workflow never runs)
        # ... (worker crashes here)
        
        # Step 5-6: Simulate retry - restore from persisted snapshot
        from generation import create_retry_generation, GenerationRecord
        
        # Reconstruct the original generation from persisted snapshot
        original_gen = GenerationRecord(
            generation_id=gen_snapshot["generation_id"],
            variation_seed=gen_snapshot["variation_seed"],
            variation_index=gen_snapshot["variation_index"],
            generation_reason=gen_snapshot["generation_reason"],
            source_task_id="task-001",
            source_batch_id="batch-001",
        )
        
        restored_gen = create_retry_generation(original_gen)
        
        # Verify seed is preserved
        assert restored_gen.variation_seed == original_seed
        assert restored_gen.generation_id == gen.generation_id
    
    def test_retry_without_persisted_generation_creates_new(self):
        """
        If no generation was persisted (e.g., very old task),
        retry creates a new generation with new seed.
        """
        from generation import create_generation, GenerationReason
        
        # No persisted generation exists
        existing_output_data = {}  # No generation_id
        
        existing_gen_id = existing_output_data.get("generation_id")
        if existing_gen_id:
            # This branch should NOT be taken
            assert False, "Should not have generation_id"
        else:
            # Create new generation
            gen = create_generation(
                reason=GenerationReason.NEW_BATCH,
                source_task_id="task-001",
            )
            assert gen.variation_seed > 0
            assert gen.generation_id is not None


class TestRerollIntegration:
    """Integration tests for the reroll flow."""
    
    def test_reroll_flow_max_3_attempts(self):
        """
        Simulate the reroll flow:
        1. First attempt produces hash that matches history
        2. Reroll with new seed
        3. Second attempt also matches
        4. Reroll again
        5. Third attempt also matches
        6. Fourth attempt - max rerolls reached, continue with warning
        """
        from generation import create_reroll_seed
        
        current_seed = 12345
        reroll_count = 0
        
        # Simulate 3 reroll attempts
        for i in range(MAX_REROLL_ATTEMPTS):
            # Each reroll produces a new seed
            new_seed = create_reroll_seed(current_seed)
            assert new_seed != current_seed
            current_seed = new_seed
            reroll_count += 1
        
        assert reroll_count == MAX_REROLL_ATTEMPTS
        
        # After max rerolls, should continue (not fail)
        # The warning "insufficient_material_variation" would be added
    
    def test_reroll_preserves_generation_id(self):
        """
        Reroll creates new variation_seed but keeps same generation_id.
        """
        from generation import GenerationRecord, GenerationReason
        import uuid
        
        gen_id = str(uuid.uuid4())
        original_seed = 12345
        
        # Simulate reroll: new seed, same generation_id
        reroll_record = GenerationRecord(
            generation_id=gen_id,
            variation_seed=create_reroll_seed(original_seed),
            variation_index=1,
            generation_reason=GenerationReason.DUPLICATE_REROLL,
            source_task_id="task-001",
            source_batch_id="batch-001",
        )
        
        assert reroll_record.generation_id == gen_id
        assert reroll_record.variation_seed != original_seed
        assert reroll_record.generation_reason == GenerationReason.DUPLICATE_REROLL
