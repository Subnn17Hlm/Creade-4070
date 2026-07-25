"""Tests for generation version model, variation RNG, and hash utilities."""
import pytest
import secrets
from generation import (
    GenerationRecord,
    GenerationReason,
    create_generation,
    create_retry_generation,
    create_reroll_generation,
    VariationRNG,
    compute_material_sequence_hash,
    compute_timeline_hash,
    compute_segment_signature_hash,
    compute_material_pool_version,
)


class TestGenerationModel:
    def test_create_generation_has_unique_id(self):
        g1 = create_generation()
        g2 = create_generation()
        assert g1.generation_id != g2.generation_id

    def test_create_generation_has_random_seed(self):
        g1 = create_generation()
        g2 = create_generation()
        assert g1.variation_seed != g2.variation_seed

    def test_create_generation_initial(self):
        g = create_generation(reason=GenerationReason.INITIAL, source_task_id="t1")
        assert g.generation_reason == "initial"
        assert g.variation_index == 0
        assert g.source_task_id == "t1"

    def test_retry_preserves_seed(self):
        g = create_generation()
        retry = create_retry_generation(g)
        assert retry.generation_id == g.generation_id
        assert retry.variation_seed == g.variation_seed
        assert retry.generation_reason == "system_retry"

    def test_reroll_changes_seed(self):
        g = create_generation()
        reroll = create_reroll_generation(g)
        assert reroll.generation_id != g.generation_id
        assert reroll.variation_seed != g.variation_seed
        assert reroll.variation_index == g.variation_index + 1
        assert reroll.generation_reason == "duplicate_reroll"
        assert reroll.reroll_count == 1

    def test_reroll_records_history(self):
        g = create_generation()
        reroll = create_reroll_generation(g)
        assert len(reroll.reroll_history) == 1
        assert reroll.reroll_history[0]["old_generation_id"] == g.generation_id

    def test_to_dict_and_from_dict(self):
        g = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id="t1")
        d = g.to_dict()
        g2 = GenerationRecord.from_dict(d)
        assert g2.generation_id == g.generation_id
        assert g2.variation_seed == g.variation_seed
        assert g2.generation_reason == g.generation_reason

    def test_seed_is_63_bits(self):
        g = create_generation()
        assert g.variation_seed < 2**63
        assert g.variation_seed >= 0


class TestVariationRNG:
    def test_deterministic_same_seed(self):
        rng1 = VariationRNG(12345, "task1", "gen1")
        rng2 = VariationRNG(12345, "task1", "gen1")
        candidates = ["a", "b", "c", "d"]
        scores = [0.9, 0.7, 0.5, 0.3]
        r1 = [rng1.weighted_choice(candidates, scores, "seg", i) for i in range(4)]
        r2 = [rng2.weighted_choice(candidates, scores, "seg", i) for i in range(4)]
        assert r1 == r2

    def test_different_seed_different_results(self):
        results = set()
        for seed in range(20):
            rng = VariationRNG(seed, "task1", f"gen{seed}")
            candidates = ["a", "b", "c", "d"]
            scores = [0.8, 0.7, 0.6, 0.5]
            chosen, _ = rng.weighted_choice(candidates, scores, "seg", 0)
            results.add(chosen)
        assert len(results) > 1

    def test_weighted_choice_respects_scores(self):
        """Higher scores should be chosen more often."""
        counts = {"high": 0, "low": 0}
        for seed in range(200):
            rng = VariationRNG(seed, "task1", f"gen{seed}")
            candidates = ["high", "low"]
            scores = [0.95, 0.1]
            chosen, _ = rng.weighted_choice(candidates, scores, "seg", 0)
            counts[chosen] += 1
        assert counts["high"] > counts["low"] * 2

    def test_single_candidate_returns_it(self):
        rng = VariationRNG(42, "t", "g")
        chosen, score = rng.weighted_choice(["only"], [0.5], "seg", 0)
        assert chosen == "only"

    def test_random_float_in_range(self):
        rng = VariationRNG(42, "t", "g")
        for i in range(50):
            v = rng.random_float(1.0, 5.0, "seg", i)
            assert 1.0 <= v < 5.0

    def test_random_int_in_range(self):
        rng = VariationRNG(42, "t", "g")
        for i in range(50):
            v = rng.random_int(0, 10, "seg", i)
            assert 0 <= v <= 10

    def test_shuffle_deterministic(self):
        rng1 = VariationRNG(42, "t", "g")
        rng2 = VariationRNG(42, "t", "g")
        items = [1, 2, 3, 4, 5]
        assert rng1.shuffle(items, "s", 0) == rng2.shuffle(items, "s", 0)

    def test_shuffle_different_seed(self):
        results = set()
        for seed in range(20):
            rng = VariationRNG(seed, "t", f"g{seed}")
            result = tuple(rng.shuffle([1, 2, 3, 4, 5], "s", 0))
            results.add(result)
        assert len(results) > 1


class TestHashUtils:
    def test_material_sequence_hash_deterministic(self):
        mats = [
            {"material_id": "m1", "source_start": 0.0, "source_end": 3.0},
            {"material_id": "m2", "source_start": 1.0, "source_end": 4.0},
        ]
        h1 = compute_material_sequence_hash(mats)
        h2 = compute_material_sequence_hash(mats)
        assert h1 == h2

    def test_material_sequence_hash_changes_with_different_materials(self):
        mats1 = [{"material_id": "m1", "source_start": 0.0, "source_end": 3.0}]
        mats2 = [{"material_id": "m2", "source_start": 0.0, "source_end": 3.0}]
        assert compute_material_sequence_hash(mats1) != compute_material_sequence_hash(mats2)

    def test_material_sequence_hash_changes_with_different_start(self):
        mats1 = [{"material_id": "m1", "source_start": 0.0, "source_end": 3.0}]
        mats2 = [{"material_id": "m1", "source_start": 1.0, "source_end": 4.0}]
        assert compute_material_sequence_hash(mats1) != compute_material_sequence_hash(mats2)

    def test_timeline_hash_deterministic(self):
        tl = [
            {"material_id": "m1", "source_start": 0.0, "source_end": 3.0,
             "segment_id": "s1", "timeline_start": 0.0, "timeline_end": 3.0,
             "playback_rate": 1.0, "transition": "", "crop_mode": "center"},
        ]
        h1 = compute_timeline_hash(tl)
        h2 = compute_timeline_hash(tl)
        assert h1 == h2

    def test_segment_signature_hash(self):
        segs = [
            {"segment_id": "s1", "segment_index": 0, "material_id": "m1",
             "source_start": 0.0, "source_end": 3.0},
        ]
        h = compute_segment_signature_hash(segs)
        assert len(h) == 16

    def test_material_pool_version(self):
        mats = [
            {"asset_id": "a1", "primary_scene_tag": "travel", "duration_sec": 5.0, "enabled": True},
            {"asset_id": "a2", "primary_scene_tag": "display", "duration_sec": 3.0, "enabled": True},
        ]
        v1 = compute_material_pool_version(mats)
        v2 = compute_material_pool_version(mats)
        assert v1 == v2
        assert len(v1) == 12

    def test_material_pool_version_changes_on_add(self):
        mats1 = [{"asset_id": "a1", "primary_scene_tag": "t", "duration_sec": 5.0, "enabled": True}]
        mats2 = [
            {"asset_id": "a1", "primary_scene_tag": "t", "duration_sec": 5.0, "enabled": True},
            {"asset_id": "a2", "primary_scene_tag": "d", "duration_sec": 3.0, "enabled": True},
        ]
        assert compute_material_pool_version(mats1) != compute_material_pool_version(mats2)

    def test_discretization_reduces_sensitivity(self):
        """Small time differences should produce same hash."""
        mats1 = [{"material_id": "m1", "source_start": 0.0, "source_end": 3.0}]
        mats2 = [{"material_id": "m1", "source_start": 0.1, "source_end": 3.1}]
        # With 0.5s resolution, 0.0 and 0.1 both discretize to 0
        assert compute_material_sequence_hash(mats1) == compute_material_sequence_hash(mats2)
