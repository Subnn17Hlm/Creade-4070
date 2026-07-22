"""Integration tests for real multipart CSV upload."""
import io
import pytest
from api.batch_csv import validate_csv


class TestRealMultipartCsvUpload:
    """Test real multipart CSV upload scenarios."""

    def test_upload_chinese_csv_with_3_tasks(self):
        """Test uploading CSV with Chinese characters and 3 tasks."""
        # Create CSV content with Chinese characters
        csv_content = """script_id,title,script_text
test-001,测试标题1,这是第一条测试文案，包含中文内容。
test-002,测试标题2,这是第二条测试文案，也包含中文。
test-003,测试标题3,这是第三条测试文案，同样包含中文。"""

        # Validate CSV
        result = validate_csv(csv_content.encode('utf-8'), filename='batch.csv')
        
        # Should succeed
        assert result.success, f"Expected success, got errors: {[e.message for e in result.errors]}"
        assert len(result.rows) == 3
        assert result.rows[0]['task_id'] == 'test-001'
        assert result.rows[0]['script_text'] == '这是第一条测试文案，包含中文内容。'
        assert result.rows[0]['title'] == '测试标题1'
        assert result.rows[1]['task_id'] == 'test-002'
        assert result.rows[2]['task_id'] == 'test-003'

    def test_upload_csv_with_only_script_text(self):
        """Test uploading CSV with only required script_text column."""
        csv_content = """script_text
这是第一条文案
这是第二条文案
这是第三条文案"""

        result = validate_csv(csv_content.encode('utf-8'), filename='batch.csv')
        
        # Should succeed
        assert result.success, f"Expected success, got errors: {[e.message for e in result.errors]}"
        assert len(result.rows) == 3
        # task_id should be auto-generated
        assert result.rows[0]['task_id'] is not None
        assert result.rows[0]['script_text'] == '这是第一条文案'

    def test_upload_csv_with_script_id_alias(self):
        """Test uploading CSV with script_id instead of task_id."""
        csv_content = """script_id,script_text,title
id-001,文案内容1,标题1
id-002,文案内容2,标题2"""

        result = validate_csv(csv_content.encode('utf-8'), filename='batch.csv')
        
        # Should succeed
        assert result.success, f"Expected success, got errors: {[e.message for e in result.errors]}"
        assert len(result.rows) == 2
        assert result.rows[0]['task_id'] == 'id-001'
        assert result.rows[1]['task_id'] == 'id-002'

    def test_upload_csv_with_utf8_bom(self):
        """Test uploading CSV with UTF-8 BOM."""
        csv_content = """script_id,script_text,title
id-001,第一条文案,标题1
id-002,第二条文案,标题2"""

        # Add UTF-8 BOM
        bom = b'\xef\xbb\xbf'
        content = bom + csv_content.encode('utf-8')

        result = validate_csv(content, filename='batch.csv')
        
        # Should succeed
        assert result.success, f"Expected success, got errors: {[e.message for e in result.errors]}"
        assert len(result.rows) == 2

    def test_upload_csv_missing_script_text(self):
        """Test uploading CSV missing required script_text column."""
        csv_content = """script_id,title
id-001,标题1
id-002,标题2"""

        result = validate_csv(csv_content.encode('utf-8'), filename='batch.csv')
        
        # Should fail with detailed error
        assert not result.success
        assert len(result.errors) > 0
        assert 'script_text' in result.errors[0].message

    def test_upload_csv_empty_script_text_row(self):
        """Test uploading CSV with empty script_text in a row."""
        csv_content = """script_id,script_text,title
id-001,第一条文案,标题1
id-002,,标题2
id-003,第三条文案,标题3"""

        result = validate_csv(csv_content.encode('utf-8'), filename='batch.csv')
        
        # Should fail with row-specific error
        assert not result.success
        assert len(result.errors) > 0
        # Should mention row 3 and script_text
        error_messages = [e.message for e in result.errors]
        assert any('第 3 行' in msg or 'script_text 不能为空' in msg for msg in error_messages)

    def test_upload_csv_with_quoted_fields(self):
        """Test uploading CSV with quoted fields containing commas and newlines."""
        csv_content = '''script_id,script_text,title
id-001,"这是第一条文案，包含逗号",标题1
id-002,"这是第二条文案
包含换行符",标题2
id-003,这是第三条文案,标题3'''

        result = validate_csv(csv_content.encode('utf-8'), filename='batch.csv')
        
        # Should succeed
        assert result.success, f"Expected success, got errors: {[e.message for e in result.errors]}"
        assert len(result.rows) == 3
        assert '包含逗号' in result.rows[0]['script_text']
        assert '包含换行符' in result.rows[1]['script_text']

    def test_upload_csv_duplicate_script_id(self):
        """Test uploading CSV with duplicate script_id."""
        csv_content = """script_id,script_text
id-001,第一条文案
id-001,第二条文案"""

        result = validate_csv(csv_content.encode('utf-8'), filename='batch.csv')
        
        # Should fail with duplicate error
        assert not result.success
        assert len(result.errors) > 0
        error_messages = [e.message for e in result.errors]
        assert any('重复' in msg or 'id-001' in msg for msg in error_messages)

    def test_upload_csv_auto_generate_task_id(self):
        """Test that task_id is auto-generated when not provided."""
        csv_content = """script_text,title
第一条文案,标题1
第二条文案,标题2"""

        result = validate_csv(csv_content.encode('utf-8'), filename='batch.csv')
        
        # Should succeed
        assert result.success, f"Expected success, got errors: {[e.message for e in result.errors]}"
        assert len(result.rows) == 2
        # task_id should be auto-generated (8 character UUID prefix)
        assert result.rows[0]['task_id'] is not None
        assert len(result.rows[0]['task_id']) == 8
        assert result.rows[1]['task_id'] is not None
        assert result.rows[0]['task_id'] != result.rows[1]['task_id']
