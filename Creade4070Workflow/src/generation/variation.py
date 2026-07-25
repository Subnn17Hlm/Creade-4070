"""Deterministic random generator based on variation_seed.

Same seed + same inputs → same results (idempotent within a generation).
Different seeds → different results (variation across generations).
"""
import hashlib
import random
from typing import List, Tuple, Any, Optional


class VariationRNG:
    """Deterministic random number generator seeded per generation + context."""

    def __init__(self, variation_seed: int, task_id: str = "", generation_id: str = ""):
        self._base_seed = variation_seed
        self._task_id = task_id
        self._generation_id = generation_id

    def for_context(self, segment_id: str = "", segment_index: int = 0) -> random.Random:
        """Create a context-specific RNG for a segment.
        
        Combines base seed with segment context to produce deterministic
        but segment-specific randomness.
        """
        context_str = f"{self._base_seed}:{self._generation_id}:{self._task_id}:{segment_id}:{segment_index}"
        digest = hashlib.sha256(context_str.encode("utf-8")).hexdigest()
        context_seed = int(digest[:16], 16)
        rng = random.Random(context_seed)
        return rng

    def weighted_choice(
        self,
        candidates: List[Any],
        scores: List[float],
        segment_id: str = "",
        segment_index: int = 0,
        minimum_score: float = 0.0,
    ) -> Tuple[Any, float]:
        """Select from candidates using weighted random choice.
        
        Weight = max(score, minimum_score) ** 2
        Higher scores have higher probability but don't guarantee selection.
        """
        if not candidates:
            raise ValueError("No candidates provided")
        if len(candidates) == 1:
            return candidates[0], scores[0] if scores else 0.0

        rng = self.for_context(segment_id, segment_index)
        
        # Calculate weights: score^2 ensures higher scores are more likely
        weights = []
        for i, score in enumerate(scores):
            effective_score = max(score, minimum_score)
            weights.append(effective_score ** 2)
        
        # If all weights are zero, use uniform
        total_weight = sum(weights)
        if total_weight <= 0:
            weights = [1.0] * len(candidates)
            total_weight = float(len(candidates))
        
        # Weighted random selection
        r = rng.random() * total_weight
        cumulative = 0.0
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                return candidates[i], scores[i]
        
        # Fallback (shouldn't reach here due to floating point)
        return candidates[-1], scores[-1]

    def random_float(self, low: float, high: float, segment_id: str = "", segment_index: int = 0) -> float:
        """Generate a deterministic random float in [low, high)."""
        rng = self.for_context(segment_id, segment_index)
        return rng.uniform(low, high)

    def random_int(self, low: int, high: int, segment_id: str = "", segment_index: int = 0) -> int:
        """Generate a deterministic random int in [low, high]."""
        rng = self.for_context(segment_id, segment_index)
        return rng.randint(low, high)

    def shuffle(self, items: List[Any], segment_id: str = "", segment_index: int = 0) -> List[Any]:
        """Return a deterministically shuffled copy of items."""
        rng = self.for_context(segment_id, segment_index)
        result = list(items)
        rng.shuffle(result)
        return result
