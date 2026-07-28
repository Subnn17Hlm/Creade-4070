"""
Tests for run_id isolation, safe download, and video validation.
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


class TestRunIdIsolation(unittest.TestCase):
    """Test that different run_ids create isolated directories."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.runs_base = os.path.join(self.test_dir, "runs")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_different_run_ids_create_isolated_dirs(self):
        """Two concurrent run_ids should create completely separate directories."""
        import uuid
        from graphs.shared_utils import ensure_dir
        
        run_id_1 = str(uuid.uuid4())
        run_id_2 = str(uuid.uuid4())
        
        run_dir_1 = ensure_dir(os.path.join(self.runs_base, run_id_1))
        run_dir_2 = ensure_dir(os.path.join(self.runs_base, run_id_2))
        
        # Directories should be different
        self.assertNotEqual(run_dir_1, run_dir_2)
        
        # Each should have its own temp subdirectory
        temp_dir_1 = ensure_dir(os.path.join(run_dir_1, "temp"))
        temp_dir_2 = ensure_dir(os.path.join(run_dir_2, "temp"))
        
        self.assertNotEqual(temp_dir_1, temp_dir_2)
        
        # Files in one should not affect the other
        test_file_1 = os.path.join(temp_dir_1, "concat.mp4")
        test_file_2 = os.path.join(temp_dir_2, "concat.mp4")
        
        with open(test_file_1, "w") as f:
            f.write("content_1")
        with open(test_file_2, "w") as f:
            f.write("content_2")
        
        with open(test_file_1, "r") as f:
            self.assertEqual(f.read(), "content_1")
        with open(test_file_2, "r") as f:
            self.assertEqual(f.read(), "content_2")

    def test_script_source_router_uses_run_id_from_state(self):
        """script_source_router_node should use run_id from state."""
        from graphs.nodes.script_source_router_node import script_source_router_node
        
        # Mock runtime context
        mock_runtime = MagicMock()
        mock_runtime.context = MagicMock()
        mock_runtime.context.run_id = "ctx_run_id"
        
        # State with run_id
        state = {
            "script_source": "manual",
            "script_id": "test_script",
            "run_id": "state_run_id",
            "script_text": "test",
        }
        
        # Patch RUNS_BASE to use our test directory
        with patch("graphs.nodes.script_source_router_node.RUNS_BASE", self.runs_base):
            result = script_source_router_node(state, {}, mock_runtime)
        
        # Should use run_id from state
        self.assertIn("state_run_id", result["run_dir"])
        self.assertEqual(result["run_id"], "state_run_id")

    def test_script_source_router_falls_back_to_ctx_run_id(self):
        """script_source_router_node should fallback to ctx.run_id if state has no run_id."""
        from graphs.nodes.script_source_router_node import script_source_router_node
        
        # Mock runtime context
        mock_runtime = MagicMock()
        mock_runtime.context = MagicMock()
        mock_runtime.context.run_id = "ctx_run_id_123"
        
        # State without run_id
        state = {
            "script_source": "manual",
            "script_id": "test_script",
            "script_text": "test",
        }
        
        with patch("graphs.nodes.script_source_router_node.RUNS_BASE", self.runs_base):
            result = script_source_router_node(state, {}, mock_runtime)
        
        # Should use ctx.run_id
        self.assertIn("ctx_run_id_123", result["run_dir"])
        self.assertEqual(result["run_id"], "ctx_run_id_123")

    def test_script_source_router_generates_fallback_id(self):
        """script_source_router_node should generate fallback ID if no run_id available."""
        from graphs.nodes.script_source_router_node import script_source_router_node
        
        # Mock runtime context with no run_id
        mock_runtime = MagicMock()
        mock_runtime.context = MagicMock()
        mock_runtime.context.run_id = ""
        
        # State without run_id or script_id
        state = {
            "script_source": "manual",
            "script_text": "test",
        }
        
        with patch("graphs.nodes.script_source_router_node.RUNS_BASE", self.runs_base):
            result = script_source_router_node(state, {}, mock_runtime)
        
        # Should generate a fallback ID
        self.assertIn("unknown_", result["run_dir"])


class TestVideoValidation(unittest.TestCase):
    """Test video validation with ffprobe."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_validate_nonexistent_file(self):
        """validate_video_file should reject non-existent files."""
        from graphs.shared_utils import validate_video_file
        
        result = validate_video_file("/nonexistent/video.mp4")
        
        self.assertFalse(result["valid"])
        self.assertIn("not found", result["error"].lower())

    def test_validate_empty_file(self):
        """validate_video_file should reject empty files."""
        from graphs.shared_utils import validate_video_file
        
        empty_path = os.path.join(self.test_dir, "empty.mp4")
        with open(empty_path, "w") as f:
            pass  # Create empty file
        
        result = validate_video_file(empty_path)
        
        self.assertFalse(result["valid"])
        self.assertIn("empty", result["error"].lower())

    def test_validate_valid_video(self):
        """validate_video_file should accept valid video files."""
        from graphs.shared_utils import validate_video_file
        
        # Create a minimal valid video using ffmpeg
        video_path = os.path.join(self.test_dir, "valid.mp4")
        
        # Use ffmpeg to create a 1-second test video
        import subprocess
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
                "-f", "lavfi", "-i", "sine=duration=1",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac",
                video_path
            ], capture_output=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.skipTest("ffmpeg not available")
        
        if not os.path.exists(video_path):
            self.skipTest("ffmpeg failed to create test video")
        
        result = validate_video_file(video_path, min_duration=0.1)
        
        self.assertTrue(result["valid"], f"Expected valid, got error: {result['error']}")
        self.assertGreater(result["duration"], 0)
        self.assertGreater(result["width"], 0)
        self.assertGreater(result["height"], 0)
        self.assertTrue(result["codec"])

    def test_validate_corrupted_video(self):
        """validate_video_file should reject corrupted video files."""
        from graphs.shared_utils import validate_video_file
        
        # Create a corrupted "video" file
        corrupted_path = os.path.join(self.test_dir, "corrupted.mp4")
        with open(corrupted_path, "wb") as f:
            f.write(b"This is not a valid video file, just random bytes " * 100)
        
        result = validate_video_file(corrupted_path)
        
        self.assertFalse(result["valid"])
        # Should have some error about invalid format or no streams


class TestBatchExecutorRunIdPassing(unittest.TestCase):
    """Test that batch executor passes run_id correctly."""

    def test_workflow_input_includes_run_id(self):
        """Batch executor should include run_id in workflow_input."""
        from api.batch_executor import BatchExecutor
        
        # We can't easily test the full execution, but we can verify
        # the code structure by checking the source
        import inspect
        source = inspect.getsource(BatchExecutor._execute_single_task)
        
        # Verify run_id is passed to workflow_input (either inline or via build_workflow_input)
        # After refactoring, run_id is set after build_workflow_input call
        self.assertTrue(
            '"run_id": str(run_id)' in source or 'workflow_input["run_id"]' in source,
            "run_id should be set in workflow_input"
        )


if __name__ == "__main__":
    unittest.main()
