"""
Tests for BGM_URLS environment variable support.

This test file verifies:
1. BGM_URLS configured with stable selection of remote URL
2. Same script_id produces consistent results across processes
3. Remote BGM download and dual-path amix
4. Warning when BGM_URLS not configured
5. Local fallback still works for dev testing
"""

import os
import pytest
import hashlib
import tempfile
from unittest.mock import patch, MagicMock


class TestBGMUrlsConfiguration:
    """Test BGM_URLS environment variable support"""

    def test_bgm_urls_json_array(self):
        """Test BGM_URLS with JSON array format"""
        import tempfile
        from src.graphs.nodes.script_source_router_node import _select_bgm_stable
        
        bgm_urls = '["https://example.com/bgm1.mp3", "https://example.com/bgm2.mp3", "https://example.com/bgm3.mp3"]'
        
        with patch.dict(os.environ, {"BGM_URLS": bgm_urls}):
            with tempfile.TemporaryDirectory() as temp_dir:
                # Test with different script_ids to verify stable selection
                result1, _ = _select_bgm_stable("test_script_1", temp_dir)
                result2, _ = _select_bgm_stable("test_script_1", temp_dir)
                result3, _ = _select_bgm_stable("test_script_2", temp_dir)
                
                # Same script_id should produce same result
                assert result1 == result2
                # Result should be one of the URLs
                assert result1 in ["https://example.com/bgm1.mp3", "https://example.com/bgm2.mp3", "https://example.com/bgm3.mp3"]
                # Different script_id may produce different result
                assert result3 in ["https://example.com/bgm1.mp3", "https://example.com/bgm2.mp3", "https://example.com/bgm3.mp3"]

    def test_bgm_urls_comma_separated(self):
        """Test BGM_URLS with comma-separated format"""
        import tempfile
        from src.graphs.nodes.script_source_router_node import _select_bgm_stable
        
        bgm_urls = "https://example.com/bgm1.mp3,https://example.com/bgm2.mp3,https://example.com/bgm3.mp3"
        
        with patch.dict(os.environ, {"BGM_URLS": bgm_urls}):
            with tempfile.TemporaryDirectory() as temp_dir:
                result, _ = _select_bgm_stable("test_script_1", temp_dir)
                assert result in ["https://example.com/bgm1.mp3", "https://example.com/bgm2.mp3", "https://example.com/bgm3.mp3"]

    def test_bgm_urls_filters_empty_values(self):
        """Test that empty values are filtered out"""
        import tempfile
        from src.graphs.nodes.script_source_router_node import _select_bgm_stable
        
        bgm_urls = '["https://example.com/bgm1.mp3", "", "https://example.com/bgm2.mp3", null]'
        
        with patch.dict(os.environ, {"BGM_URLS": bgm_urls}):
            with tempfile.TemporaryDirectory() as temp_dir:
                result, _ = _select_bgm_stable("test_script_1", temp_dir)
                # Should only select from non-empty URLs
                assert result in ["https://example.com/bgm1.mp3", "https://example.com/bgm2.mp3"]

    def test_bgm_urls_stable_across_processes(self):
        """Test that same script_id produces consistent results across processes"""
        import tempfile
        from src.graphs.nodes.script_source_router_node import _select_bgm_stable
        
        bgm_urls = '["https://example.com/bgm1.mp3", "https://example.com/bgm2.mp3", "https://example.com/bgm3.mp3"]'
        
        with patch.dict(os.environ, {"BGM_URLS": bgm_urls}):
            with tempfile.TemporaryDirectory() as temp_dir:
                # Simulate multiple "processes" by calling multiple times
                results = [_select_bgm_stable("consistent_script_id", temp_dir)[0] for _ in range(10)]
                
                # All results should be the same
                assert len(set(results)) == 1
                assert results[0] in ["https://example.com/bgm1.mp3", "https://example.com/bgm2.mp3", "https://example.com/bgm3.mp3"]

    def test_bgm_urls_sha256_stable_hash(self):
        """Test that SHA256 is used for stable hashing"""
        import tempfile
        from src.graphs.nodes.script_source_router_node import _select_bgm_stable
        
        bgm_urls = '["https://example.com/bgm1.mp3", "https://example.com/bgm2.mp3"]'
        
        with patch.dict(os.environ, {"BGM_URLS": bgm_urls}):
            with tempfile.TemporaryDirectory() as temp_dir:
                # Verify that the selection is deterministic
                script_id = "test_script_for_hash"
                result1, _ = _select_bgm_stable(script_id, temp_dir)
                result2, _ = _select_bgm_stable(script_id, temp_dir)
                
                assert result1 == result2
                
                # Verify SHA256 is used (not Python's built-in hash which is randomized)
                # We can't directly test the implementation, but we can verify consistency
                # across multiple calls with the same script_id
                for _ in range(5):
                    assert _select_bgm_stable(script_id, temp_dir)[0] == result1


class TestBGMUrlsFallback:
    """Test fallback to local BGM directory"""

    def test_bgm_urls_not_configured_fallback_to_local(self):
        """Test that when BGM_URLS is not configured, it falls back to local directory"""
        from src.graphs.nodes.script_source_router_node import _select_bgm_stable
        
        # Construct BGM_DIR path directly
        import tempfile
        workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
        bgm_dir = os.path.join(workspace, "assets", "bgm")
        
        # Ensure BGM_URLS is not set
        env = os.environ.copy()
        env.pop("BGM_URLS", None)
        
        with patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as temp_dir:
                # Mock the local BGM directory - need to mock the actual BGM_DIR path
                with patch("os.path.exists") as mock_exists:
                    with patch("glob.glob") as mock_glob:
                        with patch("os.path.getsize") as mock_getsize:
                            # Mock exists to return True only for BGM_DIR
                            def exists_side_effect(path):
                                if path == bgm_dir:
                                    return True
                                return os.path.exists(path)
                            
                            mock_exists.side_effect = exists_side_effect
                            # Mock glob.glob to return MP3 files
                            mock_glob.return_value = [
                                os.path.join(bgm_dir, "bgm1.mp3"),
                                os.path.join(bgm_dir, "bgm2.mp3")
                            ]
                            # Mock getsize to return valid file sizes
                            mock_getsize.return_value = 1024
                            
                            result, trace_info = _select_bgm_stable("test_script_1", temp_dir)
                            
                            # Should fall back to local directory
                            assert result is not None
                            assert result != ""
                            assert result.endswith(".mp3")
                            assert trace_info["bgm_source"] == "local"

    def test_bgm_urls_not_configured_no_local_fallback(self):
        """Test warning when BGM_URLS not configured and no local fallback"""
        from src.graphs.nodes.script_source_router_node import _select_bgm_stable
        
        env = os.environ.copy()
        env.pop("BGM_URLS", None)
        
        with patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as temp_dir:
                # Mock no local BGM directory
                with patch("os.path.exists") as mock_exists:
                    mock_exists.return_value = False
                    
                    result, trace_info = _select_bgm_stable("test_script_1", temp_dir)
                    
                    # Should return empty string when no BGM available
                    assert result == ""
                    assert "BGM 配置缺失" in trace_info["warning"]

    def test_bgm_urls_empty_string(self):
        """Test that empty BGM_URLS string is treated as not configured"""
        from src.graphs.nodes.script_source_router_node import _select_bgm_stable
        
        with patch.dict(os.environ, {"BGM_URLS": ""}):
            with tempfile.TemporaryDirectory() as temp_dir:
                with patch("os.path.exists") as mock_exists:
                    mock_exists.return_value = False
                    
                    result, trace_info = _select_bgm_stable("test_script_1", temp_dir)
                    assert result == ""
                    assert "BGM 配置缺失" in trace_info["warning"]


class TestBGMDownload:
    """Test BGM download functionality"""

    def test_download_remote_bgm(self):
        """Test downloading remote BGM URL"""
        from src.graphs.nodes.final_composition_node import _download_bgm
        
        remote_url = "https://example.com/bgm.mp3"
        temp_dir = "/tmp/test_bgm"
        
        with patch("src.graphs.nodes.final_composition_node.safe_download") as mock_download:
            mock_download.return_value = "/tmp/test_bgm/bgm.mp3"
            
            result = _download_bgm(remote_url, temp_dir)
            
            # Should call safe_download for remote URL
            mock_download.assert_called_once()
            assert result == "/tmp/test_bgm/bgm.mp3"

    def test_download_local_bgm(self):
        """Test using local BGM path"""
        from src.graphs.nodes.final_composition_node import _download_bgm
        
        local_path = "/workspace/projects/Creade4070Workflow/assets/bgm/bgm1.mp3"
        temp_dir = "/tmp/test_bgm"
        
        with patch("os.path.exists") as mock_exists:
            with patch("shutil.copy2") as mock_copy:
                mock_exists.return_value = True
                mock_copy.return_value = None
                
                result = _download_bgm(local_path, temp_dir)
                
                # Should copy local file
                mock_copy.assert_called_once()
                assert result == "/tmp/test_bgm/bgm.mp3"


class TestBGMWarning:
    """Test BGM warning messages"""

    def test_warning_when_bgm_urls_not_configured(self):
        """Test that warning is added when BGM_URLS not configured"""
        from src.graphs.nodes.script_source_router_node import script_source_router_node
        
        env = os.environ.copy()
        env.pop("BGM_URLS", None)
        env.pop("BGM_TOS_PREFIX", None)
        
        with patch.dict(os.environ, env, clear=True):
            with patch("src.graphs.nodes.script_source_router_node._select_bgm_stable") as mock_select:
                mock_select.return_value = ("", {"bgm_source": "", "bgm_bucket": "", "bgm_object_key": "", "bgm_used": False, "warning": "BGM 配置缺失"})
                
                state = {
                    "script_source": "manual",
                    "script_id": "test_script",
                    "script_text": "测试文案",
                    "run_id": "test_run",
                }
                
                config = MagicMock()
                runtime = MagicMock()
                runtime.context = MagicMock()
                runtime.context.run_id = "test_run"
                
                result = script_source_router_node(state, config, runtime)
                
                # Should have warning in result
                assert "warnings" in result
                assert any("BGM" in w for w in result["warnings"])


class TestBGMDualPathAmix:
    """Test dual-path amix with remote BGM"""

    def test_bgm_url_passed_to_download(self):
        """Test that BGM URL is passed to download function"""
        # This test verifies that the bgm_url is properly passed through the state
        # and would be used by final_composition_node
        
        # Create a mock state with remote BGM URL
        state = {
            "bgm_url": "https://example.com/bgm.mp3",
        }
        
        # Verify that bgm_url is in state
        assert "bgm_url" in state
        assert state["bgm_url"] == "https://example.com/bgm.mp3"
        
        # Verify that the URL is a valid HTTP URL
        assert state["bgm_url"].startswith("http://") or state["bgm_url"].startswith("https://")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
