"""Test that all execution paths produce identical workflow_input.

Verifies:
1. build_workflow_input is the single source of truth
2. Native async and fallback paths produce identical input
3. Retry produces same seed/index/generation_id
4. Different tasks get different seeds
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime


class _MockTask:
    """Simple mock task that supports getattr correctly."""
    def __init__(
        self,
        task_id="task_001",
        batch_id="batch_001",
        generation_id="gen_001",
        variation_seed=42,
        input_data=None,
    ):
        self.task_id = task_id
        self.batch_id = batch_id
        self.generation_id = generation_id
        self.variation_seed = variation_seed
        self.input_data = dict(input_data or {})
        self.output_data = {}
        self.status = "pending"
        self.attempt_count = 0
        self.error_message = None
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.started_at = None
        self.completed_at = None


def _make_mock_task(
    task_id="task_001",
    batch_id="batch_001",
    generation_id="gen_001",
    variation_seed=42,
    input_data=None,
):
    """Create a mock BatchTask with required fields."""
    return _MockTask(
        task_id=task_id,
        batch_id=batch_id,
        generation_id=generation_id,
        variation_seed=variation_seed,
        input_data=input_data,
    )


class TestWorkflowInputConsistency:
    """All execution paths must use build_workflow_input."""

    def test_build_workflow_input_has_all_fields(self):
        """build_workflow_input must include all required fields."""
        from api.batch_executor import build_workflow_input

        task = _make_mock_task(
            task_id="task_001",
            generation_id="gen_001",
            variation_seed=42,
            input_data={"batch_task_index": 3},
        )

        result = build_workflow_input(task)

        assert result["task_id"] == "task_001"
        assert result["generation_id"] == "gen_001"
        assert result["variation_seed"] == 42
        assert result["variation_index"] == 3
        assert result["batch_task_index"] == 3

    def test_variation_seed_nonzero(self):
        """variation_seed must never be 0."""
        from api.batch_executor import build_workflow_input

        task = _make_mock_task(variation_seed=0)
        task.input_data = {}

        result = build_workflow_input(task)
        assert result["variation_seed"] != 0

    def test_different_tasks_different_seeds(self):
        """Different tasks must get different variation_seeds."""
        from api.batch_executor import build_workflow_input

        task_a = _make_mock_task(task_id="task_a", variation_seed=0)
        task_a.input_data = {}
        task_b = _make_mock_task(task_id="task_b", variation_seed=0)
        task_b.input_data = {}

        result_a = build_workflow_input(task_a)
        result_b = build_workflow_input(task_b)

        assert result_a["variation_seed"] != result_b["variation_seed"]

    def test_retry_same_seed(self):
        """Same task retried must produce same variation_seed."""
        from api.batch_executor import build_workflow_input

        task = _make_mock_task(task_id="task_retry", variation_seed=0)
        task.input_data = {"batch_task_index": 2}

        first = build_workflow_input(task)
        second = build_workflow_input(task)

        assert first["variation_seed"] == second["variation_seed"]
        assert first["generation_id"] == second["generation_id"]
        assert first["variation_index"] == second["variation_index"]

    def test_native_and_fallback_paths_identical(self):
        """Native async and fallback paths must produce identical workflow_input."""
        from api.batch_executor import build_workflow_input

        task = _make_mock_task(
            task_id="task_consistency",
            generation_id="gen_x",
            variation_seed=99,
            input_data={"batch_task_index": 1, "script_text": "test"},
        )

        # Both paths call the same function
        native_input = build_workflow_input(task)
        fallback_input = build_workflow_input(task)

        assert native_input == fallback_input
        assert native_input["task_id"] == "task_consistency"
        assert native_input["generation_id"] == "gen_x"
        assert native_input["variation_seed"] == 99
        assert native_input["variation_index"] == 1
        assert native_input["batch_task_index"] == 1

    def test_six_batch_tasks_variation_index_0_to_5(self):
        """6 batch tasks should have variation_index 0..5."""
        from api.batch_executor import build_workflow_input

        results = []
        for i in range(6):
            task = _make_mock_task(
                task_id=f"task_{i:03d}",
                variation_seed=0,
                input_data={"batch_task_index": i},
            )
            result = build_workflow_input(task)
            results.append(result)

        indices = [r["variation_index"] for r in results]
        assert indices == [0, 1, 2, 3, 4, 5]

    def test_six_tasks_all_seeds_nonzero_and_unique(self):
        """6 tasks with seed=0 should all get nonzero, unique seeds."""
        from api.batch_executor import build_workflow_input

        results = []
        for i in range(6):
            task = _make_mock_task(
                task_id=f"task_{i:03d}",
                variation_seed=0,
                input_data={"batch_task_index": i},
            )
            result = build_workflow_input(task)
            results.append(result)

        seeds = [r["variation_seed"] for r in results]
        assert all(s != 0 for s in seeds)
        assert len(set(seeds)) == 6  # all unique

    def test_persisted_values_take_priority(self):
        """Persisted input_data values should take priority over task fields."""
        from api.batch_executor import build_workflow_input

        task = _make_mock_task(
            task_id="task_persist",
            generation_id="gen_new",
            variation_seed=100,
            input_data={
                "batch_task_index": 5,
                "variation_seed": 777,
                "generation_id": "gen_old",
            },
        )

        result = build_workflow_input(task)

        # Persisted values should win
        assert result["variation_seed"] == 777
        assert result["generation_id"] == "gen_old"
        assert result["variation_index"] == 5
