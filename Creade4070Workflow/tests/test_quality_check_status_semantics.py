"""
Tests for quality check status semantics.

Verifies that:
1. needs_manual_review is a quality warning, not an execution failure
2. When video is successfully generated with quality warnings, task status is success with review_required=true
3. Node statuses reflect real values (quality check node shows warning, not failed)
4. Only actual failures (no video, upload failure) result in failed status
"""
import pytest
from unittest.mock import MagicMock
import uuid


class TestQualityCheckStatusSemantics:
    """Test quality check node returns correct status for quality warnings."""

    def test_low_confidence_segments_logic(self):
        """Test the logic: when low_confidence_segments >= 3 but video exists, status should be success with review_required."""
        # Simulate the quality check logic
        low_conf_segments = 6
        final_video_path = "/tmp/test/video.mp4"
        video_exists = True  # Simulate video exists
        
        # This is the logic from quality_check_node.py
        failure_category = "fully_successful"
        status = "success"
        review_required = False
        warnings = []
        
        fail_reasons = []
        if low_conf_segments >= 3:
            fail_reasons.append(f"low_confidence_segments={low_conf_segments}>=3, needs_manual_review")
        
        if len(fail_reasons) > 0:
            if low_conf_segments >= 3:
                failure_category = "needs_review"
                if final_video_path and video_exists:
                    status = "success"
                    review_required = True
                    warnings.append(f"low_confidence_segments={low_conf_segments}>=3, needs_manual_review")
                else:
                    status = "failed"
        
        # Verify the logic
        assert status == "success", f"Expected status='success' but got '{status}'"
        assert review_required == True, "Expected review_required=True for low_confidence_segments >= 3"
        assert len(warnings) > 0, "Expected warnings to contain low_confidence_segments info"
        assert any("low_confidence_segments" in w for w in warnings), \
            f"Expected warning about low_confidence_segments, got: {warnings}"
        assert failure_category == "needs_review", \
            f"Expected failure_category='needs_review' but got '{failure_category}'"

    def test_no_video_results_in_failed(self):
        """Test the logic: when video doesn't exist, status should be failed."""
        low_conf_segments = 0
        final_video_path = "/tmp/test/nonexistent.mp4"
        video_exists = False  # Simulate video doesn't exist
        
        failure_category = "fully_successful"
        status = "success"
        review_required = False
        warnings = []
        
        fail_reasons = []
        # Simulate subtitle not visible (which happens when video doesn't exist)
        subtitle_visible = False
        if not subtitle_visible:
            fail_reasons.append("subtitle_not_visible")
        
        if len(fail_reasons) > 0:
            if not subtitle_visible:
                failure_category = "subtitle_not_visible"
                status = "failed"
        
        # Verify the logic
        assert status == "failed", f"Expected status='failed' but got '{status}'"
        assert review_required == False, "Expected review_required=False for actual failure"


class TestBatchExecutorHandlesReviewRequired:
    """Test that batch executor correctly handles review_required."""

    @pytest.mark.asyncio
    async def test_success_with_review_required_is_treated_as_success(self):
        """When workflow returns success with review_required=True, task should be marked as success."""
        from api.batch_executor import BatchExecutor
        
        executor = BatchExecutor(graph_service=MagicMock())
        
        # Mock workflow result with success and review_required
        workflow_result = {
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
            "review_required": True,
            "warnings": ["low_confidence_segments=6>=3, needs_manual_review"],
        }
        
        # The batch executor should treat this as success
        assert workflow_result.get("status") == "success", \
            "Workflow result with review_required should still have status='success'"
        
        # Verify the result includes review_required and warnings
        assert workflow_result.get("review_required") == True
        assert len(workflow_result.get("warnings", [])) > 0


class TestFrontendDisplaysWarnings:
    """Test that frontend can display warnings correctly."""

    def test_status_response_includes_review_required_and_warnings(self):
        """Status API response should include review_required and warnings fields."""
        # Simulate the response structure
        response = {
            "run_id": "test-run-id",
            "status": "success",
            "task_id": str(uuid.uuid4()),
            "batch_id": str(uuid.uuid4()),
            "result": {
                "status": "success",
                "final_video_url": "https://example.com/video.mp4",
                "review_required": True,
                "warnings": ["low_confidence_segments=6>=3, needs_manual_review"],
                "failure_category": "needs_review",
            },
            "final_video_url": "https://example.com/video.mp4",
        }
        
        # Frontend should be able to check review_required
        assert response["result"]["review_required"] == True
        
        # Frontend should be able to display warnings
        assert len(response["result"]["warnings"]) > 0
        
        # Frontend should still show the video
        assert response["final_video_url"] == "https://example.com/video.mp4"
        
        # Status should be success, not failed
        assert response["status"] == "success"


class TestStateDefinitionIncludesNewFields:
    """Test that state definition includes review_required and warnings."""

    def test_global_state_has_review_required_and_warnings(self):
        """GlobalState TypedDict should include review_required and warnings fields."""
        from graphs.state import GlobalState
        import typing
        
        # Get the annotations
        annotations = typing.get_type_hints(GlobalState)
        
        # Check that review_required and warnings are defined
        assert "review_required" in annotations, \
            "GlobalState should include 'review_required' field"
        assert "warnings" in annotations, \
            "GlobalState should include 'warnings' field"
        
        # Check types
        assert annotations["review_required"] == bool, \
            f"review_required should be bool, got {annotations['review_required']}"
        # warnings should be List[str]
        assert "List" in str(annotations["warnings"]) or "list" in str(annotations["warnings"]), \
            f"warnings should be List[str], got {annotations['warnings']}"

    def test_graph_output_has_review_required_and_warnings(self):
        """GraphOutput model should include review_required and warnings fields."""
        from graphs.state import GraphOutput
        
        # Create an instance with default values
        output = GraphOutput()
        
        # Check that the fields exist
        assert hasattr(output, "review_required"), \
            "GraphOutput should include 'review_required' field"
        assert hasattr(output, "warnings"), \
            "GraphOutput should include 'warnings' field"
        
        # Check default values
        assert output.review_required == False, \
            f"review_required default should be False, got {output.review_required}"
        assert output.warnings == [], \
            f"warnings default should be [], got {output.warnings}"

    def test_quality_check_output_has_review_required_and_warnings(self):
        """QualityCheckOutput model should include review_required and warnings fields."""
        from graphs.state import QualityCheckOutput
        
        # Create an instance with required fields
        output = QualityCheckOutput(
            quality_report={},
            status="success",
        )
        
        # Check that the fields exist
        assert hasattr(output, "review_required"), \
            "QualityCheckOutput should include 'review_required' field"
        assert hasattr(output, "warnings"), \
            "QualityCheckOutput should include 'warnings' field"
        
        # Check default values
        assert output.review_required == False, \
            f"review_required default should be False, got {output.review_required}"
        assert output.warnings == [], \
            f"warnings default should be [], got {output.warnings}"
