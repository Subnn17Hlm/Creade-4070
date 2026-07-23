"""
Test helpers for batch page frontend logic.
These functions mirror the frontend JavaScript logic for testing.
"""
import json


def render_task_card_html(task):
    """
    Render a task card as HTML string.
    Mirrors the frontend renderTaskCard function.
    """
    task_id = task.get('task_id', '')
    status = task.get('status', '')
    script_id = task.get('script_id')
    title = task.get('title')
    script_text = task.get('script_text', '')
    final_video_url = task.get('final_video_url')
    warning = task.get('warning')
    error_code = task.get('error_code')
    error_message = task.get('error_message')
    retry_count = task.get('retry_count', 0)

    status_labels = {
        'created': '等待', 'pending': '等待', 'queued': '等待',
        'running': '运行中', 'success': '成功', 'failed': '失败',
    }
    status_label = status_labels.get(status, status)

    parts = [f'<div class="task-card" data-task-id="{task_id}">']
    parts.append(f'<span class="task-id">{task_id}</span>')

    if script_id:
        parts.append(f'<span class="script-id">{script_id}</span>')
    if title:
        parts.append(f'<span class="task-title">{title}</span>')

    if script_text:
        snippet = script_text[:80] + ('...' if len(script_text) > 80 else '')
        parts.append(f'<div class="script-text">{snippet}</div>')

    parts.append(f'<span class="status status-{status}">{status_label}</span>')

    if warning:
        parts.append(f'<span class="warning">{warning}</span>')

    if error_code:
        parts.append(f'<span class="error-code">{error_code}</span>')
    if error_message:
        parts.append(f'<span class="error-message">{error_message}</span>')

    if retry_count:
        parts.append(f'<span class="retry-count">重试 {retry_count} 次</span>')

    if status == 'success':
        if final_video_url:
            parts.append(f'<a class="view-video-btn" href="{final_video_url}" target="_blank" rel="noopener">查看视频</a>')
        else:
            parts.append('<span class="no-video-url">任务成功，但视频地址尚未回写</span>')

    if status == 'failed':
        parts.append(f'<button class="retry-btn" data-task-id="{task_id}">重试</button>')

    parts.append('</div>')
    return '\n'.join(parts)


def parse_tasks_response(response):
    """
    Parse tasks from API response.
    Supports: {tasks: [...]}, {data: {tasks: [...]}}, or [...]
    Returns None for unexpected structures.
    """
    if isinstance(response, list):
        return response

    if isinstance(response, dict):
        if 'tasks' in response:
            tasks = response['tasks']
            if isinstance(tasks, list):
                return tasks
        if 'data' in response and isinstance(response['data'], dict):
            tasks = response['data'].get('tasks')
            if isinstance(tasks, list):
                return tasks

    return None


def is_retry_success(status_code):
    """Check if retry response status code indicates success."""
    return 200 <= status_code < 300


def extract_error_message(status_code, body):
    """Extract error message from response body."""
    if body is None:
        return f'HTTP {status_code}'

    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return f'HTTP {status_code}: {body}'

    if isinstance(body, dict):
        msg = body.get('detail') or body.get('message') or body.get('error')
        if msg:
            return str(msg)

    return f'HTTP {status_code}'


class RetryGuard:
    """Prevents duplicate retry submissions."""

    def __init__(self):
        self._in_flight = set()

    def try_acquire(self, task_id):
        if task_id in self._in_flight:
            return False
        self._in_flight.add(task_id)
        return True

    def release(self, task_id):
        self._in_flight.discard(task_id)


def should_resubmit_after_anomaly(current_status):
    """
    After a response anomaly, check if we should resubmit.
    If task is already queued/running, don't resubmit.
    """
    return current_status in ('failed', 'pending', 'created')


def is_start_success(status_code):
    """Check if start batch response indicates success."""
    return 200 <= status_code < 300


def parse_start_response(status_code, body):
    """Parse start batch response. Empty body is OK for 2xx."""
    if 200 <= status_code < 300:
        return {'success': True, 'status_code': status_code}

    # Error case
    msg = extract_error_message(status_code, body)
    return {'success': False, 'status_code': status_code, 'error': msg}


def should_restart_after_anomaly(tasks):
    """
    After response anomaly, check if we should restart.
    If any task has moved to queued/running/success/failed, don't restart.
    """
    active_statuses = {'queued', 'running', 'success', 'failed'}
    for task in tasks:
        if task.get('status') in active_statuses:
            return False
    return True


def csv_escape(value):
    """
    Escape a value for CSV.
    - Wraps in double quotes if contains comma, double quote, or newline
    - Doubles internal double quotes
    - Returns empty string for None
    """
    if value is None:
        return ''

    s = str(value)

    needs_quoting = ',' in s or '"' in s or '\n' in s or '\r' in s

    if needs_quoting:
        s = s.replace('"', '""')
        return f'"{s}"'

    return s


def generate_csv(tasks):
    """
    Generate CSV content from tasks list.
    Includes UTF-8 BOM.
    """
    BOM = '\ufeff'

    headers = [
        'task_id', 'batch_id', 'script_id', 'title', 'script_text',
        'status', 'final_video_url', 'warning', 'error_code',
        'error_message', 'retry_count', 'run_id', 'async_task_id',
        'created_at', 'started_at', 'completed_at', 'updated_at',
    ]

    lines = [','.join(headers)]

    for task in tasks:
        row = []
        for h in headers:
            val = task.get(h)
            row.append(csv_escape(val))
        lines.append(','.join(row))

    return BOM + '\n'.join(lines)
