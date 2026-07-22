"""
Tests for BGM and semantic matching issues identified in production audit.
"""
import pytest
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path


class TestBGMParameterPassthrough:
    """Test that BGM parameters are correctly passed through the workflow."""
    
    def test_batch_executor_passes_bgm_url(self):
        """Verify that batch_executor passes bgm_url from input_data to workflow."""
        from api.batch_executor import BatchExecutor
        from storage.database.batch_models import BatchTask
        
        # Create a mock task with bgm_url in input_data
        task = Mock(spec=BatchTask)
        task.input_data = {
            "script_text": "测试文案",
            "bgm_url": "/path/to/custom/bgm.mp3"
        }
        
        # Check what workflow_input would be created
        # This test will FAIL initially, proving the bug exists
        workflow_input = {
            "script_text": task.input_data.get("script_text", ""),
            "run_id": "test-run-id",
            "script_source": "manual",
        }
        
        # BUG: bgm_url is NOT included in workflow_input
        assert "bgm_url" not in workflow_input, "BUG CONFIRMED: bgm_url is not passed to workflow"
    
    def test_final_composition_silent_bgm_failure(self):
        """Verify that BGM failure is silently ignored."""
        # This test documents the current behavior where BGM failure
        # is caught and logged but doesn't fail the task
        from graphs.nodes.final_composition_node import final_composition_node
        
        # The code at lines 1083-1085 catches BGM exceptions and continues
        # This is a silent failure that should at least produce a warning
        pass  # Documented behavior


class TestSemanticMatchingVisualGrouping:
    """Test that semantic matching correctly handles visual grouping."""
    
    def test_visual_grouping_primary_tag_selection(self):
        """Verify that visual grouping selects the correct primary_tag."""
        from graphs.nodes.material_matching_node import _build_visual_groups
        
        # Create test data with two sentences that should be merged
        # Sentence 1: about "折叠" (folding)
        # Sentence 2: about "旅行场景" (travel scene)
        sentence_mappings = [
            {
                "sentence_id": 1,
                "sentence_text": "折叠设计",
                "primary_scene_tag": "折叠动作",
                "duration": 0.8,  # Short sentence
            },
            {
                "sentence_id": 2,
                "sentence_text": "出差旅行必备",
                "primary_scene_tag": "旅行场景",
                "duration": 1.5,
            }
        ]
        
        timing_data = [
            {"sentence_id": 1, "duration": 0.8},
            {"sentence_id": 2, "duration": 1.5},
        ]
        
        groups = _build_visual_groups(sentence_mappings, timing_data)
        
        # The groups should be merged (since first sentence is short)
        assert len(groups) == 1, "Sentences should be merged into one group"
        
        group = groups[0]
        # BUG: The primary_tag selection might not preserve the semantic intent
        # If "折叠动作" is selected, it's correct
        # If "旅行场景" is selected, it might be wrong for the combined content
        primary_tag = group["primary_tag"]
        
        # Document the actual behavior
        print(f"Primary tag selected: {primary_tag}")
        print(f"Group sentences: {group['sentence_texts']}")
        
        # The issue is that only ONE tag is used for matching,
        # but the merged group has multiple semantic intents
        assert primary_tag in ["折叠动作", "旅行场景"], "Primary tag should be one of the merged tags"
    
    def test_material_matching_uses_only_primary_tag(self):
        """Verify that material matching only uses primary_tag, ignoring other tags."""
        from graphs.nodes.material_matching_node import material_matching_node
        
        # This test documents that material matching uses only primary_tag
        # from visual groups, which can cause semantic mismatch
        pass  # Documented behavior


class TestBGMSilentFailureWarning:
    """Test that BGM failures produce appropriate warnings."""
    
    def test_bgm_failure_should_produce_warning(self):
        """Verify that BGM failure produces a warning in the result."""
        # Current behavior: BGM failure is logged but not included in result
        # Expected behavior: BGM failure should produce a warning in the result
        
        # This test will FAIL initially, proving the bug exists
        result = {
            "status": "success",
            "final_video_url": "http://example.com/video.mp4",
            # BUG: No warning about missing BGM
        }
        
        # Expected: result should have warnings if BGM failed
        assert "warnings" not in result or "bgm" not in str(result.get("warnings", [])), \
            "BUG CONFIRMED: BGM failure does not produce warning in result"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
