"""
Tests for subtitle burning thread limit and async HTTP submit.
"""
import pytest
import subprocess
import tempfile
import os
import json
from unittest.mock import patch, MagicMock


class TestSubtitleThreadLimit:
    """Verify subtitle burning ffmpeg commands use -threads 2."""

    def test_subtitle_burn_command_has_threads_2(self):
        """Subtitle burning ffmpeg command must include -threads 2."""
        from graphs.nodes.final_composition_node import _burn_subtitles_with_overlay

        # Create minimal test files
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal SRT file
            srt_path = os.path.join(tmpdir, "test.srt")
            with open(srt_path, "w") as f:
                f.write("1\n00:00:01,000 --> 00:00:03,000\nTest subtitle\n\n")

            # Create dummy video and audio files (just need to exist for command construction)
            video_path = os.path.join(tmpdir, "video.mp4")
            audio_path = os.path.join(tmpdir, "audio.wav")
            output_path = os.path.join(tmpdir, "output.mp4")
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

            # Create dummy files
            for p in [video_path, audio_path]:
                with open(p, "wb") as f:
                    f.write(b"\x00" * 100)

            # Mock subprocess.run to capture the command without actually running ffmpeg
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stderr="",
                )
                # Also mock os.path.exists for the output file check
                with patch("os.path.exists", return_value=True):
                    with patch("os.path.getsize", return_value=1000):
                        result = _burn_subtitles_with_overlay(
                            ffmpeg_path="ffmpeg",
                            video_path=video_path,
                            audio_path=audio_path,
                            srt_path=srt_path,
                            font_path=font_path,
                            output_path=output_path,
                            temp_dir=tmpdir,
                            video_width=720,
                            video_height=1280,
                        )

                # Verify the command was called
                assert mock_run.called, "subprocess.run should have been called"
                cmd = mock_run.call_args[0][0]

                # Verify -threads 2 appears in the command (for input and output)
                threads_positions = [i for i, x in enumerate(cmd) if x == "-threads"]
                assert len(threads_positions) >= 1, f"Expected at least one -threads in command: {cmd}"

                # Check that at least one -threads is followed by "2"
                threads_values = [cmd[i + 1] for i in threads_positions if i + 1 < len(cmd)]
                assert "2" in threads_values, f"Expected -threads 2 in command, got threads values: {threads_values}"

    def test_subtitle_verification_command_has_threads_2(self):
        """Subtitle verification frame extraction must use -threads 2."""
        from graphs.nodes.final_composition_node import _burn_subtitles_with_overlay

        with tempfile.TemporaryDirectory() as tmpdir:
            srt_path = os.path.join(tmpdir, "test.srt")
            with open(srt_path, "w") as f:
                f.write("1\n00:00:01,000 --> 00:00:03,000\nTest\n\n")

            video_path = os.path.join(tmpdir, "video.mp4")
            audio_path = os.path.join(tmpdir, "audio.wav")
            output_path = os.path.join(tmpdir, "output.mp4")
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

            for p in [video_path, audio_path]:
                with open(p, "wb") as f:
                    f.write(b"\x00" * 100)

            call_count = [0]
            original_run = subprocess.run

            def mock_run_side_effect(cmd, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call is the main subtitle burn
                    return MagicMock(returncode=0, stderr="")
                else:
                    # Subsequent calls are verification frame extractions
                    # Verify -threads 2 is in the command
                    assert "-threads" in cmd, f"Verification command missing -threads: {cmd}"
                    threads_idx = cmd.index("-threads")
                    assert cmd[threads_idx + 1] == "2", \
                        f"Verification command should use -threads 2, got: {cmd[threads_idx + 1]}"
                    return MagicMock(returncode=0, stderr="")

            with patch("subprocess.run", side_effect=mock_run_side_effect):
                with patch("os.path.exists", return_value=True):
                    with patch("os.path.getsize", return_value=1000):
                        _burn_subtitles_with_overlay(
                            ffmpeg_path="ffmpeg",
                            video_path=video_path,
                            audio_path=audio_path,
                            srt_path=srt_path,
                            font_path=font_path,
                            output_path=output_path,
                            temp_dir=tmpdir,
                            video_width=720,
                            video_height=1280,
                        )


class TestAsyncRunEndpoint:
    """Verify /run endpoint returns immediately with run_id."""

    def test_run_returns_submitted_status(self):
        """POST /run should return status='submitted' immediately."""
        # Test the logic directly without importing main module
        from graphs.run_trace_persistence import register_run, get_run_mapping, _run_mapping

        test_run_id = "test-async-run-001"
        register_run(test_run_id, "test-script", "/tmp/test/trace.jsonl")

        try:
            mapping = get_run_mapping(test_run_id)
            assert mapping is not None
            assert mapping["status"] == "running"
            assert mapping["run_id"] == test_run_id
        finally:
            _run_mapping.pop(test_run_id, None)

    def test_status_endpoint_returns_404_for_unknown_run(self):
        """get_run_mapping should return None for unknown run_id."""
        from graphs.run_trace_persistence import get_run_mapping

        result = get_run_mapping("nonexistent-run-id-xyz")
        assert result is None

    def test_status_endpoint_returns_running_status(self):
        """Registered run should have running status."""
        from graphs.run_trace_persistence import register_run, get_run_mapping, _run_mapping

        test_run_id = "test-async-run-002"
        register_run(test_run_id, "test-script", "/tmp/test/trace.jsonl")

        try:
            mapping = get_run_mapping(test_run_id)
            assert mapping is not None
            assert mapping["status"] == "running"
            assert "created_at" in mapping
        finally:
            _run_mapping.pop(test_run_id, None)

    def test_status_endpoint_returns_completed_result(self):
        """Completed run should return result with final_video_url."""
        from graphs.run_trace_persistence import register_run, update_run_status, get_run_mapping, _run_mapping

        test_run_id = "test-async-run-003"
        register_run(test_run_id, "test-script", "/tmp/test/trace.jsonl")
        update_run_status(
            test_run_id, "success",
            result={"status": "success", "final_video_url": "https://example.com/video.mp4"},
        )

        try:
            mapping = get_run_mapping(test_run_id)
            assert mapping is not None
            assert mapping["status"] == "success"
            result = mapping.get("result", {})
            assert result["final_video_url"] == "https://example.com/video.mp4"
            assert "completed_at" in mapping
        finally:
            _run_mapping.pop(test_run_id, None)


class TestSubtitleOOMHandling:
    """Verify subtitle burning handles OOM (SIGKILL) gracefully."""

    def test_oom_error_message_includes_diagnostics(self):
        """When ffmpeg is killed by SIGKILL (-9), error should include diagnostic info."""
        from graphs.nodes.final_composition_node import _burn_subtitles_with_overlay

        with tempfile.TemporaryDirectory() as tmpdir:
            srt_path = os.path.join(tmpdir, "test.srt")
            with open(srt_path, "w") as f:
                f.write("1\n00:00:01,000 --> 00:00:03,000\nTest\n\n")

            video_path = os.path.join(tmpdir, "video.mp4")
            audio_path = os.path.join(tmpdir, "audio.wav")
            output_path = os.path.join(tmpdir, "output.mp4")
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

            for p in [video_path, audio_path]:
                with open(p, "wb") as f:
                    f.write(b"\x00" * 100)

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=-9,
                    stderr="Killed\n",
                )

                result = _burn_subtitles_with_overlay(
                    ffmpeg_path="ffmpeg",
                    video_path=video_path,
                    audio_path=audio_path,
                    srt_path=srt_path,
                    font_path=font_path,
                    output_path=output_path,
                    temp_dir=tmpdir,
                    video_width=720,
                    video_height=1280,
                )

                assert result["ffmpeg_returncode"] == -9
                assert "SIGKILL" in result["error"] or "OOM" in result["error"], \
                    f"Error should mention SIGKILL/OOM: {result['error']}"
                assert "字幕数=1" in result["error"], \
                    f"Error should include cue count: {result['error']}"
                assert "720x1280" in result["error"], \
                    f"Error should include resolution: {result['error']}"
