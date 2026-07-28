"""CSV validation for batch tasks."""
import csv
import io
from dataclasses import dataclass
from typing import List, Optional, Tuple


MAX_BATCH_SIZE = 50
REQUIRED_COLUMNS = ['script_text']  # Only script_text is required
OPTIONAL_COLUMNS = ['script_id', 'title', 'task_id']  # task_id is alias for script_id


@dataclass
class CSVValidationError:
    """CSV validation error."""
    row_number: Optional[int]
    field: Optional[str]
    message: str
    
    def to_dict(self) -> dict:
        return {
            'row_number': self.row_number,
            'field': self.field,
            'message': self.message,
        }


@dataclass
class CSVParseResult:
    """CSV parse result."""
    success: bool
    rows: List[dict]
    errors: List[CSVValidationError]
    
    def to_dict(self) -> dict:
        return {
            'success': self.success,
            'rows': self.rows,
            'errors': [e.to_dict() for e in self.errors],
        }


def validate_csv(content: bytes, filename: Optional[str] = None) -> CSVParseResult:
    """Validate CSV content for batch tasks.
    
    Args:
        content: CSV file content as bytes
        filename: Optional filename for error messages
        
    Returns:
        CSVParseResult with validation result
    """
    errors: List[CSVValidationError] = []
    rows: List[dict] = []
    
    # Check for empty content
    if not content or len(content.strip()) == 0:
        errors.append(CSVValidationError(
            row_number=None,
            field=None,
            message='CSV 文件为空',
        ))
        return CSVParseResult(success=False, rows=[], errors=errors)
    
    # Decode content (handle UTF-8 BOM)
    try:
        if content.startswith(b'\xef\xbb\xbf'):
            text = content[3:].decode('utf-8')
        else:
            text = content.decode('utf-8')
    except UnicodeDecodeError as e:
        errors.append(CSVValidationError(
            row_number=None,
            field=None,
            message=f'文件编码错误，请使用 UTF-8 编码: {e}',
        ))
        return CSVParseResult(success=False, rows=[], errors=errors)
    
    # Parse CSV
    try:
        reader = csv.DictReader(io.StringIO(text))
        
        # Check headers
        if reader.fieldnames is None:
            errors.append(CSVValidationError(
                row_number=None,
                field=None,
                message='CSV 文件缺少表头',
            ))
            return CSVParseResult(success=False, rows=[], errors=errors)
        
        # Normalize headers (strip whitespace)
        headers = [h.strip() for h in reader.fieldnames]
        
        # Check required columns
        missing_columns = [col for col in REQUIRED_COLUMNS if col not in headers]
        if missing_columns:
            errors.append(CSVValidationError(
                row_number=None,
                field=None,
                message=f'缺少必填列: {", ".join(missing_columns)}。需要的列: {", ".join(REQUIRED_COLUMNS)}',
            ))
            return CSVParseResult(success=False, rows=[], errors=errors)
        
        # Parse rows
        seen_task_ids = set()
        row_number = 1  # Start from 1 (after header)
        
        for row in reader:
            row_number += 1
            
            # Normalize keys
            normalized_row = {k.strip(): v for k, v in row.items() if k is not None}
            
            # Check script_text (required)
            script_text = normalized_row.get('script_text', '').strip()
            if not script_text:
                errors.append(CSVValidationError(
                    row_number=row_number,
                    field='script_text',
                    message=f'第 {row_number} 行: script_text 不能为空',
                ))
                continue
            
            # Get or generate task_id
            # Support both task_id and script_id as aliases
            task_id = normalized_row.get('task_id', '').strip()
            if not task_id:
                task_id = normalized_row.get('script_id', '').strip()
            if not task_id:
                # Auto-generate task_id if not provided
                import uuid
                task_id = str(uuid.uuid4())[:8]
            
            # Check duplicate task_id (only if explicitly provided)
            if task_id in seen_task_ids:
                errors.append(CSVValidationError(
                    row_number=row_number,
                    field='task_id',
                    message=f'第 {row_number} 行: task_id/script_id "{task_id}" 重复',
                ))
                continue
            seen_task_ids.add(task_id)
            
            # Get optional title
            title = normalized_row.get('title', '').strip()
            
            # Add valid row
            rows.append({
                'row_number': row_number - 1,  # 1-indexed row number (excluding header)
                'batch_task_index': len(rows),  # 0-indexed position among valid rows (0, 1, 2, ...)
                'task_id': task_id,
                'script_text': script_text,
                'title': title,
            })
        
        # Check max batch size
        if len(rows) > MAX_BATCH_SIZE:
            errors.append(CSVValidationError(
                row_number=None,
                field=None,
                message=f'批次任务数量超过上限 {MAX_BATCH_SIZE}，当前: {len(rows)}',
            ))
            return CSVParseResult(success=False, rows=[], errors=errors)
        
        # Check if any rows were parsed
        if len(rows) == 0 and len(errors) == 0:
            errors.append(CSVValidationError(
                row_number=None,
                field=None,
                message='CSV 文件没有有效数据行',
            ))
        
    except csv.Error as e:
        errors.append(CSVValidationError(
            row_number=None,
            field=None,
            message=f'CSV 解析错误: {e}',
        ))
        return CSVParseResult(success=False, rows=[], errors=errors)
    
    success = len(errors) == 0 and len(rows) > 0
    return CSVParseResult(success=success, rows=rows, errors=errors)
