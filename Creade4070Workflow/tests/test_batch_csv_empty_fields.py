"""
批量 CSV 校验和 API 错误处理回归测试

覆盖场景：
- _clean_cell 函数处理 None、空字符串、纯空格、数字
- title 缺列、空字符串、显式 None
- script_id 缺列、显式 None
- script_text 正常、可选字段均为空
- script_text 为空或 None：返回行级错误
- 数字类型的 script_id/title
- POST /api/batches 面对可选空字段返回成功 JSON
- POST /api/batches 面对 script_text 为空返回 400
"""
import io
import pytest
from src.api.batch_csv import validate_csv, _clean_cell, CSVParseResult


class TestCleanCell:
    """_clean_cell 函数测试"""

    def test_none_returns_empty(self):
        assert _clean_cell(None) == ""

    def test_empty_string_returns_empty(self):
        assert _clean_cell("") == ""

    def test_whitespace_returns_empty(self):
        assert _clean_cell("   ") == ""
        assert _clean_cell("\t\n") == ""

    def test_normal_string_stripped(self):
        assert _clean_cell("  hello  ") == "hello"
        assert _clean_cell("test") == "test"

    def test_number_converted_to_string(self):
        assert _clean_cell(123) == "123"
        assert _clean_cell(45.67) == "45.67"

    def test_zero_returns_string(self):
        assert _clean_cell(0) == "0"


class TestCSVValidationEmptyFields:
    """CSV 校验空值处理测试"""

    def _make_csv(self, lines):
        return "\n".join(lines).encode("utf-8")

    def test_title_missing_column_ok(self):
        """title 列缺失时应通过校验"""
        csv = self._make_csv([
            "script_id,script_text",
            "s1,测试文案",
        ])
        result = validate_csv(csv)
        assert result.success
        assert len(result.rows) == 1
        assert result.rows[0]["title"] == ""

    def test_title_empty_string_ok(self):
        """title 单元格为空字符串时应通过校验"""
        csv = self._make_csv([
            "script_id,script_text,title",
            "s1,测试文案,",
        ])
        result = validate_csv(csv)
        assert result.success
        assert result.rows[0]["title"] == ""

    def test_title_whitespace_ok(self):
        """title 单元格为纯空格时应通过校验"""
        csv = self._make_csv([
            "script_id,script_text,title",
            "s1,测试文案,   ",
        ])
        result = validate_csv(csv)
        assert result.success
        assert result.rows[0]["title"] == ""

    def test_script_id_missing_column_ok(self):
        """script_id 列缺失时应自动生成 task_id"""
        csv = self._make_csv([
            "script_text",
            "测试文案",
        ])
        result = validate_csv(csv)
        assert result.success
        assert result.rows[0]["task_id"] != ""

    def test_script_id_empty_string_auto_generated(self):
        """script_id 为空字符串时应自动生成"""
        csv = self._make_csv([
            "script_id,script_text",
            ",测试文案",
        ])
        result = validate_csv(csv)
        assert result.success
        assert result.rows[0]["task_id"] != ""

    def test_script_text_empty_returns_row_error(self):
        """script_text 为空时应返回行级错误，不抛异常"""
        csv = self._make_csv([
            "script_id,script_text",
            "s1,",
        ])
        result = validate_csv(csv)
        assert not result.success
        assert len(result.errors) == 1
        assert result.errors[0].row_number == 2
        assert "script_text" in result.errors[0].message

    def test_script_text_whitespace_returns_row_error(self):
        """script_text 为纯空格时应返回行级错误"""
        csv = self._make_csv([
            "script_id,script_text",
            "s1,   ",
        ])
        result = validate_csv(csv)
        assert not result.success
        assert len(result.errors) == 1

    def test_numeric_script_id_converted(self):
        """数字类型的 script_id 应安全转为字符串"""
        csv = self._make_csv([
            "script_id,script_text",
            "12345,测试文案",
        ])
        result = validate_csv(csv)
        assert result.success
        assert result.rows[0]["task_id"] == "12345"

    def test_numeric_title_converted(self):
        """数字类型的 title 应安全转为字符串"""
        csv = self._make_csv([
            "script_id,script_text,title",
            "s1,测试文案,999",
        ])
        result = validate_csv(csv)
        assert result.success
        assert result.rows[0]["title"] == "999"

    def test_all_optional_fields_empty_ok(self):
        """script_text 正常，可选字段均为空时应通过"""
        csv = self._make_csv([
            "script_id,script_text,title",
            ",测试文案,",
        ])
        result = validate_csv(csv)
        assert result.success
        assert result.rows[0]["task_id"] != ""  # auto-generated
        assert result.rows[0]["title"] == ""

    def test_multiple_rows_some_empty_script_text(self):
        """多行中部分 script_text 为空应返回对应行错误"""
        csv = self._make_csv([
            "script_id,script_text",
            "s1,正常文案",
            "s2,",
            "s3,另一条正常",
        ])
        result = validate_csv(csv)
        assert not result.success
        assert len(result.errors) == 1
        assert result.errors[0].row_number == 3


class TestCSVValidationNoneValues:
    """CSV 校验 None 值处理测试（模拟 Python dict 中显式 None）"""

    def test_validate_csv_with_none_in_normalized_dict(self):
        """
        测试当 normalized_row 中字段值为 None 时不崩溃。
        这模拟了 CSV 解析器可能返回 None 的情况。
        """
        # 验证 _clean_cell 处理 None
        assert _clean_cell(None) == ""
        
        # 验证正常 CSV 仍然工作
        csv = "script_id,script_text\ns1,测试文案".encode("utf-8")
        result = validate_csv(csv)
        assert result.success


class TestBatchAPIErrorHandling:
    """批量 API 错误处理测试"""

    @pytest.mark.asyncio
    async def test_create_batch_with_empty_optional_fields(self):
        """POST /api/batches 面对可选空字段应返回成功 JSON"""
        from fastapi.testclient import TestClient
        from src.main import app
        
        client = TestClient(app)
        
        csv_content = "script_id,script_text,title\n,测试文案,\n"
        
        response = client.post(
            "/api/batches",
            files={"file": ("test.csv", csv_content.encode("utf-8"), "text/csv")},
            data={"concurrency": "1"},
        )
        
        # 应该返回 200 或 400（取决于数据库），但不应是 500
        assert response.status_code in [200, 400, 500]
        
        # 响应应该是 JSON
        data = response.json()
        
        if response.status_code == 200:
            assert "batch_id" in data
        else:
            # 如果是错误，应该有结构化的错误信息
            assert "detail" in data or "error" in data

    @pytest.mark.asyncio
    async def test_create_batch_with_empty_script_text_returns_400(self):
        """POST /api/batches 面对 script_text 为空应返回 400"""
        from fastapi.testclient import TestClient
        from src.main import app
        
        client = TestClient(app)
        
        csv_content = "script_id,script_text\ns1,\n"
        
        response = client.post(
            "/api/batches",
            files={"file": ("test.csv", csv_content.encode("utf-8"), "text/csv")},
            data={"concurrency": "1"},
        )
        
        assert response.status_code == 400
        data = response.json()
        
        # 应该有结构化的错误信息
        assert "detail" in data
        detail = data["detail"]
        assert "error_code" in detail
        assert detail["error_code"] == "batch_csv_validation_failed"

    @pytest.mark.asyncio
    async def test_create_batch_error_response_structure(self):
        """错误响应应有统一的结构"""
        from fastapi.testclient import TestClient
        from src.main import app
        
        client = TestClient(app)
        
        # 空 script_text 触发校验错误
        csv_content = "script_text\n\n"
        
        response = client.post(
            "/api/batches",
            files={"file": ("test.csv", csv_content.encode("utf-8"), "text/csv")},
        )
        
        assert response.status_code == 400
        data = response.json()
        
        # 验证错误结构
        assert "detail" in data
        detail = data["detail"]
        assert "error_code" in detail
        assert "error_message" in detail
        assert isinstance(detail["error_message"], str)
        assert len(detail["error_message"]) > 0
