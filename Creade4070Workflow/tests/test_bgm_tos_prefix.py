"""
BGM TOS Prefix 支持测试
测试 BGM_TOS_PREFIX 环境变量的解析、TOS 对象列举、过滤、稳定选择和下载
"""
import os
import json
import tempfile
import hashlib
import pytest
from unittest.mock import patch, MagicMock
from src.graphs.nodes.script_source_router_node import (
    _parse_bgm_tos_prefix,
    _list_bgm_from_tos,
    _download_bgm_from_tos,
    _select_bgm_stable,
)


class TestBGMTosPrefixParsing:
    """测试 BGM_TOS_PREFIX 解析"""

    def test_parse_valid_prefix(self):
        """测试有效的 tos://bucket/prefix 格式"""
        with patch.dict(os.environ, {"BGM_TOS_PREFIX": "tos://coze-video-assets-hlm/bgm/"}):
            bucket, prefix = _parse_bgm_tos_prefix()
            assert bucket == "coze-video-assets-hlm"
            assert prefix == "bgm/"

    def test_parse_prefix_without_trailing_slash(self):
        """测试没有尾部斜杠的 prefix"""
        with patch.dict(os.environ, {"BGM_TOS_PREFIX": "tos://my-bucket/bgm"}):
            bucket, prefix = _parse_bgm_tos_prefix()
            assert bucket == "my-bucket"
            assert prefix == "bgm"

    def test_parse_bucket_only(self):
        """测试只有 bucket 没有 prefix"""
        with patch.dict(os.environ, {"BGM_TOS_PREFIX": "tos://my-bucket"}):
            bucket, prefix = _parse_bgm_tos_prefix()
            assert bucket == "my-bucket"
            assert prefix == ""

    def test_parse_empty_prefix(self):
        """测试空环境变量"""
        with patch.dict(os.environ, {"BGM_TOS_PREFIX": ""}):
            bucket, prefix = _parse_bgm_tos_prefix()
            assert bucket == ""
            assert prefix == ""

    def test_parse_invalid_format(self):
        """测试无效格式（不以 tos:// 开头）"""
        with patch.dict(os.environ, {"BGM_TOS_PREFIX": "s3://bucket/prefix"}):
            bucket, prefix = _parse_bgm_tos_prefix()
            assert bucket == ""
            assert prefix == ""

    def test_parse_missing_bucket(self):
        """测试缺少 bucket"""
        with patch.dict(os.environ, {"BGM_TOS_PREFIX": "tos:///prefix"}):
            bucket, prefix = _parse_bgm_tos_prefix()
            assert bucket == ""
            assert prefix == ""


class TestBGMTosObjectListing:
    """测试 TOS 对象列举和过滤"""

    def test_list_objects_filter_mp3(self):
        """测试只保留 .mp3 文件"""
        mock_objects = [
            {"key": "bgm/song1.mp3", "size": 1000000, "last_modified": "2024-01-01"},
            {"key": "bgm/song2.wav", "size": 2000000, "last_modified": "2024-01-02"},
            {"key": "bgm/song3.mp3", "size": 3000000, "last_modified": "2024-01-03"},
            {"key": "bgm/readme.txt", "size": 100, "last_modified": "2024-01-04"},
        ]
        
        with patch("storage.tos.tos_client.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.list_objects.return_value = mock_objects
            mock_get_client.return_value = mock_client
            
            keys = _list_bgm_from_tos("my-bucket", "bgm/")
            
            assert len(keys) == 2
            assert keys == ["bgm/song1.mp3", "bgm/song3.mp3"]

    def test_list_objects_filter_size(self):
        """测试过滤大小为 0 的对象"""
        mock_objects = [
            {"key": "bgm/song1.mp3", "size": 1000000, "last_modified": "2024-01-01"},
            {"key": "bgm/song2.mp3", "size": 0, "last_modified": "2024-01-02"},
            {"key": "bgm/song3.mp3", "size": 3000000, "last_modified": "2024-01-03"},
        ]
        
        with patch("storage.tos.tos_client.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.list_objects.return_value = mock_objects
            mock_get_client.return_value = mock_client
            
            keys = _list_bgm_from_tos("my-bucket", "bgm/")
            
            assert len(keys) == 2
            assert keys == ["bgm/song1.mp3", "bgm/song3.mp3"]

    def test_list_objects_filter_directory_placeholder(self):
        """测试过滤目录占位对象"""
        mock_objects = [
            {"key": "bgm/song1.mp3", "size": 1000000, "last_modified": "2024-01-01"},
            {"key": "bgm/subdir/", "size": 0, "last_modified": "2024-01-02"},
            {"key": "bgm/song2.mp3", "size": 3000000, "last_modified": "2024-01-03"},
        ]
        
        with patch("storage.tos.tos_client.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.list_objects.return_value = mock_objects
            mock_get_client.return_value = mock_client
            
            keys = _list_bgm_from_tos("my-bucket", "bgm/")
            
            assert len(keys) == 2
            assert keys == ["bgm/song1.mp3", "bgm/song2.mp3"]

    def test_list_objects_sorted(self):
        """测试对象按 key 排序"""
        mock_objects = [
            {"key": "bgm/song3.mp3", "size": 3000000, "last_modified": "2024-01-03"},
            {"key": "bgm/song1.mp3", "size": 1000000, "last_modified": "2024-01-01"},
            {"key": "bgm/song2.mp3", "size": 2000000, "last_modified": "2024-01-02"},
        ]
        
        with patch("storage.tos.tos_client.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.list_objects.return_value = mock_objects
            mock_get_client.return_value = mock_client
            
            keys = _list_bgm_from_tos("my-bucket", "bgm/")
            
            assert keys == ["bgm/song1.mp3", "bgm/song2.mp3", "bgm/song3.mp3"]

    def test_list_objects_client_not_configured(self):
        """测试 TOS 客户端未配置"""
        with patch("storage.tos.tos_client.get_client") as mock_get_client:
            mock_get_client.return_value = None
            
            keys = _list_bgm_from_tos("my-bucket", "bgm/")
            
            assert keys == []

    def test_list_objects_exception(self):
        """测试列举异常"""
        with patch("storage.tos.tos_client.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.list_objects.side_effect = Exception("TOS error")
            mock_get_client.return_value = mock_client
            
            keys = _list_bgm_from_tos("my-bucket", "bgm/")
            
            assert keys == []


class TestBGMTosDownload:
    """测试 TOS BGM 下载"""

    def test_download_success(self):
        """测试成功下载"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("storage.tos.tos_client.get_client") as mock_get_client:
                mock_client = MagicMock()
                mock_client.download_object.return_value = None
                mock_get_client.return_value = mock_client
                
                local_path = _download_bgm_from_tos("my-bucket", "bgm/song.mp3", temp_dir)
                
                assert local_path.endswith("song.mp3")
                assert os.path.dirname(local_path) == temp_dir
                mock_client.download_object.assert_called_once_with(
                    bucket="my-bucket",
                    object_key="bgm/song.mp3",
                    local_path=local_path
                )

    def test_download_client_not_configured(self):
        """测试 TOS 客户端未配置"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("storage.tos.tos_client.get_client") as mock_get_client:
                mock_get_client.return_value = None
                
                with pytest.raises(Exception, match="TOS 客户端未配置"):
                    _download_bgm_from_tos("my-bucket", "bgm/song.mp3", temp_dir)

    def test_download_exception(self):
        """测试下载异常"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("storage.tos.tos_client.get_client") as mock_get_client:
                mock_client = MagicMock()
                mock_client.download_object.side_effect = Exception("Download failed")
                mock_get_client.return_value = mock_client
                
                with pytest.raises(Exception, match="Download failed"):
                    _download_bgm_from_tos("my-bucket", "bgm/song.mp3", temp_dir)


class TestBGMStableSelectionWithTos:
    """测试包含 TOS 的稳定选择"""

    def test_stable_selection_with_tos(self):
        """测试从 TOS 稳定选择 BGM"""
        script_id = "test-script-123"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"BGM_TOS_PREFIX": "tos://my-bucket/bgm/"}):
                with patch("src.graphs.nodes.script_source_router_node._list_bgm_from_tos") as mock_list:
                    with patch("src.graphs.nodes.script_source_router_node._download_bgm_from_tos") as mock_download:
                        mock_list.return_value = ["bgm/song1.mp3", "bgm/song2.mp3", "bgm/song3.mp3"]
                        mock_download.return_value = os.path.join(temp_dir, "song2.mp3")
                        
                        bgm_path, trace_info = _select_bgm_stable(script_id, temp_dir)
                        
                        assert bgm_path == os.path.join(temp_dir, "song2.mp3")
                        assert trace_info["bgm_source"] == "tos"
                        assert trace_info["bgm_bucket"] == "my-bucket"
                        assert trace_info["bgm_used"] is True
                        assert trace_info["warning"] == ""

    def test_stable_selection_consistency(self):
        """测试同一 script_id 跨进程稳定选择"""
        script_id = "test-script-456"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"BGM_TOS_PREFIX": "tos://my-bucket/bgm/"}):
                with patch("src.graphs.nodes.script_source_router_node._list_bgm_from_tos") as mock_list:
                    with patch("src.graphs.nodes.script_source_router_node._download_bgm_from_tos") as mock_download:
                        mock_list.return_value = ["bgm/song1.mp3", "bgm/song2.mp3", "bgm/song3.mp3"]
                        mock_download.return_value = os.path.join(temp_dir, "song.mp3")
                        
                        # 多次调用应该选择相同的 BGM
                        results = []
                        for _ in range(5):
                            bgm_path, trace_info = _select_bgm_stable(script_id, temp_dir)
                            results.append(trace_info["bgm_object_key"])
                        
                        # 所有结果应该相同
                        assert len(set(results)) == 1

    def test_priority_tos_over_bgm_urls(self):
        """测试 BGM_TOS_PREFIX 优先级高于 BGM_URLS"""
        script_id = "test-script-789"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {
                "BGM_TOS_PREFIX": "tos://my-bucket/bgm/",
                "BGM_URLS": '["https://example.com/song.mp3"]'
            }):
                with patch("src.graphs.nodes.script_source_router_node._list_bgm_from_tos") as mock_list:
                    with patch("src.graphs.nodes.script_source_router_node._download_bgm_from_tos") as mock_download:
                        mock_list.return_value = ["bgm/song.mp3"]
                        mock_download.return_value = os.path.join(temp_dir, "song.mp3")
                        
                        bgm_path, trace_info = _select_bgm_stable(script_id, temp_dir)
                        
                        assert trace_info["bgm_source"] == "tos"
                        assert trace_info["bgm_used"] is True

    def test_fallback_to_bgm_urls_when_tos_fails(self):
        """测试 TOS 列举失败时 fallback 到 BGM_URLS"""
        script_id = "test-script-101"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {
                "BGM_TOS_PREFIX": "tos://my-bucket/bgm/",
                "BGM_URLS": '["https://example.com/song.mp3"]'
            }):
                with patch("src.graphs.nodes.script_source_router_node._list_bgm_from_tos") as mock_list:
                    mock_list.return_value = []  # TOS 列举失败
                    
                    bgm_path, trace_info = _select_bgm_stable(script_id, temp_dir)
                    
                    assert trace_info["bgm_source"] == "bgm_urls"
                    assert trace_info["bgm_used"] is True

    def test_fallback_to_bgm_urls_when_tos_download_fails(self):
        """测试 TOS 下载失败时 fallback 到 BGM_URLS"""
        script_id = "test-script-102"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {
                "BGM_TOS_PREFIX": "tos://my-bucket/bgm/",
                "BGM_URLS": '["https://example.com/song.mp3"]'
            }):
                with patch("src.graphs.nodes.script_source_router_node._list_bgm_from_tos") as mock_list:
                    with patch("src.graphs.nodes.script_source_router_node._download_bgm_from_tos") as mock_download:
                        mock_list.return_value = ["bgm/song.mp3"]
                        mock_download.side_effect = Exception("Download failed")
                        
                        bgm_path, trace_info = _select_bgm_stable(script_id, temp_dir)
                        
                        assert trace_info["bgm_source"] == "bgm_urls"
                        assert trace_info["bgm_used"] is True
                        assert "BGM 下载失败" in trace_info["warning"]

    def test_warning_when_all_sources_fail(self):
        """测试所有来源都失败时产生 warning"""
        script_id = "test-script-103"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {
                "BGM_TOS_PREFIX": "tos://my-bucket/bgm/",
            }):
                with patch("src.graphs.nodes.script_source_router_node._list_bgm_from_tos") as mock_list:
                    mock_list.return_value = []  # TOS 列举失败
                    
                    bgm_path, trace_info = _select_bgm_stable(script_id, temp_dir)
                    
                    assert bgm_path == ""
                    assert trace_info["bgm_source"] == ""
                    assert trace_info["bgm_used"] is False
                    assert "BGM 配置缺失" in trace_info["warning"]


class TestBGMIntegration:
    """集成测试"""

    def test_full_tos_flow(self):
        """测试完整的 TOS BGM 流程"""
        script_id = "integration-test-script"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"BGM_TOS_PREFIX": "tos://coze-video-assets-hlm/bgm/"}):
                with patch("storage.tos.tos_client.get_client") as mock_get_client:
                    # Mock TOS 客户端
                    mock_client = MagicMock()
                    mock_client.list_objects.return_value = [
                        {"key": "bgm/bgm_01.mp3", "size": 1600000, "last_modified": "2024-01-01"},
                        {"key": "bgm/bgm_02.mp3", "size": 2000000, "last_modified": "2024-01-02"},
                        {"key": "bgm/bgm_03.mp3", "size": 2900000, "last_modified": "2024-01-03"},
                    ]
                    mock_client.download_object.return_value = None
                    mock_get_client.return_value = mock_client
                    
                    bgm_path, trace_info = _select_bgm_stable(script_id, temp_dir)
                    
                    # 验证选择了 BGM
                    assert trace_info["bgm_source"] == "tos"
                    assert trace_info["bgm_bucket"] == "coze-video-assets-hlm"
                    assert trace_info["bgm_used"] is True
                    assert trace_info["bgm_object_key"] in ["bgm/bgm_01.mp3", "bgm/bgm_02.mp3", "bgm/bgm_03.mp3"]
                    
                    # 验证下载被调用
                    assert mock_client.download_object.called
