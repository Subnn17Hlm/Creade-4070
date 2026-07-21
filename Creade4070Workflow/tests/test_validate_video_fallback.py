"""
Tests for validate_video_file ffprobe/ffmpeg fallback logic.
"""
import os
import sys
import shutil
import tempfile
import subprocess
import unittest
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


class TestValidateVideoFileFallback(unittest.TestCase):
    """Test validate_video_file with ffprobe/ffmpeg fallback."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_ffprobe_returns_none_uses_ffmpeg_fallback(self):
        """When get_ffprobe_path() returns None, should use ffmpeg fallback."""
        from graphs.shared_utils import validate_video_file
        
        # Create a valid test video
        video_path = os.path.join(self.test_dir, "test.mp4")
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
                "-c:v", "libx264", "-preset", "ultrafast",
                video_path
            ], capture_output=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.skipTest("ffmpeg not available")
        
        if not os.path.exists(video_path):
            self.skipTest("ffmpeg failed to create test video")
        
        # Patch get_ffprobe_path to return None
        with patch("graphs.shared_utils.get_ffprobe_path", return_value=None):
            result = validate_video_file(video_path)
        
        self.assertTrue(result["valid"], f"Expected valid, got error: {result.get('error')}")
        self.assertEqual(result["method"], "ffmpeg_fallback")

    def test_ffprobe_available_uses_ffprobe(self):
        """When get_ffprobe_path() returns a path, should use ffprobe."""
        from graphs.shared_utils import validate_video_file
        
        # Create a valid test video
        video_path = os.path.join(self.test_dir, "test.mp4")
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
                "-c:v", "libx264", "-preset", "ultrafast",
                video_path
            ], capture_output=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.skipTest("ffmpeg not available")
        
        if not os.path.exists(video_path):
            self.skipTest("ffmpeg failed to create test video")
        
        # Check if ffprobe is available on this system
        ffprobe_available = shutil.which("ffprobe")
        if not ffprobe_available:
            self.skipTest("ffprobe not available on system")
        
        # Don't patch - let it use real ffprobe
        result = validate_video_file(video_path)
        
        self.assertTrue(result["valid"], f"Expected valid, got error: {result.get('error')}")
        self.assertEqual(result["method"], "ffprobe")
        self.assertGreater(result["width"], 0)
        self.assertGreater(result["height"], 0)

    def test_ffmpeg_fallback_rejects_corrupted_video(self):
        """ffmpeg fallback should reject corrupted video files."""
        from graphs.shared_utils import validate_video_file
        
        # Create a corrupted "video" file
        corrupted_path = os.path.join(self.test_dir, "corrupted.mp4")
        with open(corrupted_path, "wb") as f:
            f.write(b"This is not a valid video file, just random bytes " * 100)
        
        # Patch get_ffprobe_path to return None (force ffmpeg fallback)
        with patch("graphs.shared_utils.get_ffprobe_path", return_value=None):
            result = validate_video_file(corrupted_path)
        
        self.assertFalse(result["valid"])
        self.assertEqual(result["method"], "ffmpeg_fallback")
        self.assertIsNotNone(result["error"])

    def test_subprocess_never_receives_none(self):
        """Ensure subprocess.run is never called with None in command list."""
        from graphs.shared_utils import validate_video_file
        
        # Create a test file
        test_path = os.path.join(self.test_dir, "test.mp4")
        with open(test_path, "wb") as f:
            f.write(b"fake video content" * 100)
        
        original_run = subprocess.run
        captured_cmds = []
        
        def mock_subprocess_run(cmd, *args, **kwargs):
            captured_cmds.append(cmd)
            # Check that no element in cmd is None
            for i, arg in enumerate(cmd):
                self.assertIsNotNone(arg, f"subprocess.run received None at index {i} in cmd: {cmd}")
            return original_run(cmd, *args, **kwargs)
        
        with patch("graphs.shared_utils.get_ffprobe_path", return_value=None):
            with patch("subprocess.run", side_effect=mock_subprocess_run):
                validate_video_file(test_path)
        
        # Verify at least one command was captured
        self.assertGreater(len(captured_cmds), 0, "No subprocess.run calls captured")

    def test_ffmpeg_fallback_valid_video_succeeds(self):
        """ffmpeg fallback should succeed for valid video."""
        from graphs.shared_utils import validate_video_file
        
        # Create a valid test video
        video_path = os.path.join(self.test_dir, "valid.mp4")
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=2:size=640x480:rate=24",
                "-c:v", "libx264", "-preset", "ultrafast",
                video_path
            ], capture_output=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.skipTest("ffmpeg not available")
        
        if not os.path.exists(video_path):
            self.skipTest("ffmpeg failed to create test video")
        
        # Force ffmpeg fallback
        with patch("graphs.shared_utils.get_ffprobe_path", return_value=None):
            result = validate_video_file(video_path, min_duration=0.1)
        
        self.assertTrue(result["valid"], f"Expected valid, got error: {result.get('error')}")
        self.assertEqual(result["method"], "ffmpeg_fallback")
        self.assertGreater(result["file_size"], 0)

    def test_ffmpeg_fallback_invalid_video_fails(self):
        """ffmpeg fallback should fail for invalid/corrupted video."""
        from graphs.shared_utils import validate_video_file
        
        # Create an invalid file that looks like a video but isn't
        invalid_path = os.path.join(self.test_dir, "invalid.mp4")
        with open(invalid_path, "wb") as f:
            # Write some bytes that won't decode as video
            f.write(b"\x00\x00\x00\x1c\x66\x74\x79\x70" + b"\x00" * 1000)
        
        # Force ffmpeg fallback
        with patch("graphs.shared_utils.get_ffprobe_path", return_value=None):
            result = validate_video_file(invalid_path)
        
        # Should fail because ffmpeg can't decode it
        self.assertFalse(result["valid"])
        self.assertEqual(result["method"], "ffmpeg_fallback")


class TestValidateVideoFileEdgeCases(unittest.TestCase):
    """Test edge cases for validate_video_file."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_nonexistent_file(self):
        """Should reject non-existent files."""
        from graphs.shared_utils import validate_video_file
        
        result = validate_video_file("/nonexistent/path/video.mp4")
        
        self.assertFalse(result["valid"])
        self.assertIn("not found", result["error"].lower())

    def test_empty_file(self):
        """Should reject empty files."""
        from graphs.shared_utils import validate_video_file
        
        empty_path = os.path.join(self.test_dir, "empty.mp4")
        with open(empty_path, "w") as f:
            pass
        
        result = validate_video_file(empty_path)
        
        self.assertFalse(result["valid"])
        self.assertIn("empty", result["error"].lower())

    def test_method_field_always_present(self):
        """Result should always include 'method' field."""
        from graphs.shared_utils import validate_video_file
        
        # Non-existent file
        result = validate_video_file("/nonexistent/video.mp4")
        self.assertIn("method", result)
        
        # Empty file
        empty_path = os.path.join(self.test_dir, "empty.mp4")
        with open(empty_path, "w") as f:
            pass
        result = validate_video_file(empty_path)
        self.assertIn("method", result)


if __name__ == "__main__":
    unittest.main()
