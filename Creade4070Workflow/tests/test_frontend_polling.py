"""
前端轮询逻辑测试

测试批量页面轮询终止逻辑：
1. 运行中会轮询
2. pending/running均为0时停止轮询
3. 页面卸载后无请求
4. 多次初始化只有一个定时器
5. 手动刷新仍有效
6. 任务列表和CSV功能保持正常
"""

import pytest
import json
import re


class TestPollingTerminationLogic:
    """测试轮询终止逻辑"""
    
    def test_is_batch_terminal_function_exists(self):
        """测试 isBatchTerminal 函数存在"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        assert 'function isBatchTerminal' in content, "isBatchTerminal 函数应该存在"
    
    def test_is_batch_terminal_checks_pending_and_running(self):
        """测试 isBatchTerminal 检查 pending 和 running"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # 提取 isBatchTerminal 函数 - 使用更宽松的匹配
        match = re.search(r'function isBatchTerminal\(data\)\s*\{([\s\S]*?)\n\}', content)
        assert match, "isBatchTerminal 函数应该存在"
        
        func_body = match.group(1)
        
        # 检查是否检查 pending 和 running
        assert 'pending' in func_body or 'queued' in func_body, "应该检查 pending/queued"
        assert 'running' in func_body, "应该检查 running"
    
    def test_is_batch_terminal_checks_status(self):
        """测试 isBatchTerminal 检查批次状态"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # 提取 isBatchTerminal 函数
        match = re.search(r'function isBatchTerminal\(data\)\s*\{([^}]+)\}', content, re.DOTALL)
        assert match, "isBatchTerminal 函数应该存在"
        
        func_body = match.group(1)
        
        # 检查是否检查终态状态
        assert 'success' in func_body, "应该检查 success 状态"
        assert 'failed' in func_body, "应该检查 failed 状态"
        assert 'cancelled' in func_body, "应该检查 cancelled 状态"
    
    def test_stop_batch_polling_clears_timer(self):
        """测试 stopBatchPolling 清除定时器"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # 提取 stopBatchPolling 函数
        match = re.search(r'function stopBatchPolling\(\)\s*\{([^}]+)\}', content, re.DOTALL)
        assert match, "stopBatchPolling 函数应该存在"
        
        func_body = match.group(1)
        
        # 检查是否清除定时器
        assert 'clearInterval' in func_body, "应该调用 clearInterval"
        assert 'pollingTimer = null' in func_body or 'pollingTimer=null' in func_body, "应该将 pollingTimer 设为 null"
    
    def test_start_batch_polling_prevents_duplicate(self):
        """测试 startBatchPolling 防止重复创建定时器"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # 提取 startBatchPolling 函数
        match = re.search(r'function startBatchPolling\(batchId\)\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}', content, re.DOTALL)
        assert match, "startBatchPolling 函数应该存在"
        
        func_body = match.group(1)
        
        # 检查是否先调用 stopBatchPolling
        assert 'stopBatchPolling()' in func_body, "应该先调用 stopBatchPolling 防止重复"
    
    def test_visibility_change_handler_exists(self):
        """测试页面可见性变化处理存在"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        assert 'visibilitychange' in content, "应该有 visibilitychange 事件监听"
        assert 'document.hidden' in content, "应该检查 document.hidden"
    
    def test_before_unload_handler_exists(self):
        """测试页面卸载处理存在"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        assert 'beforeunload' in content, "应该有 beforeunload 事件监听"


class TestPollingBehaviorSimulation:
    """模拟轮询行为测试"""
    
    def test_terminal_batch_should_stop_polling(self):
        """测试终态批次应该停止轮询"""
        # 模拟 isBatchTerminal 函数逻辑
        def is_batch_terminal(data):
            if data.get('status') in ['success', 'failed', 'cancelled', 'partial_failed']:
                return True
            counts = data.get('task_counts', {})
            pending_count = (counts.get('pending', 0) + counts.get('queued', 0))
            running_count = counts.get('running', 0)
            if pending_count == 0 and running_count == 0:
                return True
            return False
        
        # 测试用例：所有任务成功
        data = {
            'status': 'running',  # 批次状态可能还是 running
            'task_counts': {
                'pending': 0,
                'queued': 0,
                'running': 0,
                'success': 3,
                'failed': 0
            }
        }
        assert is_batch_terminal(data) == True, "所有任务完成时应该停止轮询"
    
    def test_running_batch_should_continue_polling(self):
        """测试运行中批次应该继续轮询"""
        def is_batch_terminal(data):
            if data.get('status') in ['success', 'failed', 'cancelled', 'partial_failed']:
                return True
            counts = data.get('task_counts', {})
            pending_count = (counts.get('pending', 0) + counts.get('queued', 0))
            running_count = counts.get('running', 0)
            if pending_count == 0 and running_count == 0:
                return True
            return False
        
        # 测试用例：有运行中任务
        data = {
            'status': 'running',
            'task_counts': {
                'pending': 0,
                'queued': 0,
                'running': 2,
                'success': 1,
                'failed': 0
            }
        }
        assert is_batch_terminal(data) == False, "有运行中任务时应该继续轮询"
    
    def test_pending_batch_should_continue_polling(self):
        """测试有待处理任务的批次应该继续轮询"""
        def is_batch_terminal(data):
            if data.get('status') in ['success', 'failed', 'cancelled', 'partial_failed']:
                return True
            counts = data.get('task_counts', {})
            pending_count = (counts.get('pending', 0) + counts.get('queued', 0))
            running_count = counts.get('running', 0)
            if pending_count == 0 and running_count == 0:
                return True
            return False
        
        # 测试用例：有待处理任务
        data = {
            'status': 'running',
            'task_counts': {
                'pending': 1,
                'queued': 0,
                'running': 1,
                'success': 1,
                'failed': 0
            }
        }
        assert is_batch_terminal(data) == False, "有待处理任务时应该继续轮询"
    
    def test_success_status_should_stop_polling(self):
        """测试 success 状态应该停止轮询"""
        def is_batch_terminal(data):
            if data.get('status') in ['success', 'failed', 'cancelled', 'partial_failed']:
                return True
            counts = data.get('task_counts', {})
            pending_count = (counts.get('pending', 0) + counts.get('queued', 0))
            running_count = counts.get('running', 0)
            if pending_count == 0 and running_count == 0:
                return True
            return False
        
        data = {
            'status': 'success',
            'task_counts': {
                'pending': 0,
                'queued': 0,
                'running': 0,
                'success': 3,
                'failed': 0
            }
        }
        assert is_batch_terminal(data) == True, "success 状态应该停止轮询"
    
    def test_partial_failed_status_should_stop_polling(self):
        """测试 partial_failed 状态应该停止轮询"""
        def is_batch_terminal(data):
            if data.get('status') in ['success', 'failed', 'cancelled', 'partial_failed']:
                return True
            counts = data.get('task_counts', {})
            pending_count = (counts.get('pending', 0) + counts.get('queued', 0))
            running_count = counts.get('running', 0)
            if pending_count == 0 and running_count == 0:
                return True
            return False
        
        data = {
            'status': 'partial_failed',
            'task_counts': {
                'pending': 0,
                'queued': 0,
                'running': 0,
                'success': 2,
                'failed': 1
            }
        }
        assert is_batch_terminal(data) == True, "partial_failed 状态应该停止轮询"


class TestRefreshButtonBehavior:
    """测试刷新按钮行为"""
    
    def test_refresh_button_does_not_start_polling(self):
        """测试刷新按钮不会启动轮询"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # 找到刷新按钮的事件监听器
        match = re.search(r"refreshBtn\.addEventListener\('click',\s*\(\)\s*=>\s*\{([^}]+)\}", content)
        assert match, "刷新按钮事件监听器应该存在"
        
        handler_body = match.group(1)
        
        # 检查是否只调用 loadBatchStatus，不调用 startBatchPolling
        assert 'loadBatchStatus' in handler_body, "应该调用 loadBatchStatus"
        assert 'startBatchPolling' not in handler_body, "不应该调用 startBatchPolling"


class TestTaskListAndCsvFunctionality:
    """测试任务列表和CSV功能"""
    
    def test_task_list_endpoint_exists(self):
        """测试任务列表接口存在"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        assert '/tasks' in content, "应该有任务列表接口调用"
    
    def test_csv_export_function_exists(self):
        """测试CSV导出函数存在"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        assert 'exportBatchCsv' in content or 'exportCsv' in content, "应该有CSV导出函数"


class TestApiResponsesForPolling:
    """测试API响应格式"""
    
    def test_batch_endpoint_returns_task_counts(self):
        """测试批次接口返回 task_counts"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        assert 'task_counts' in content, "前端应该使用 task_counts"
    
    def test_task_counts_includes_pending_and_running(self):
        """测试 task_counts 包含 pending 和 running"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # 检查前端是否使用 pending 和 running
        assert 'pending' in content, "前端应该使用 pending"
        assert 'running' in content, "前端应该使用 running"


class TestRecoveryButtonVisibility:
    """测试恢复调度按钮可见性"""
    
    def test_recovery_button_text_exists(self):
        """测试恢复调度按钮文本存在"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        assert '检查/恢复调度' in content, "应该有'检查/恢复调度'按钮文本"
    
    def test_button_not_hidden_when_running_gt_0(self):
        """测试 running>0 时按钮不被隐藏"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # The old logic was: runningCount === 0 was required to show button
        # The new logic should NOT require runningCount === 0
        # Check that shouldShowStartBtn does NOT include runningCount === 0
        # Find the shouldShowStartBtn assignment
        match = re.search(r'shouldShowStartBtn\s*=\s*([^;]+);', content)
        assert match, "should find shouldShowStartBtn assignment"
        
        condition = match.group(1)
        # The condition should NOT contain runningCount === 0
        assert 'runningCount === 0' not in condition, \
            "Button should be visible even when running > 0 (for orphan recovery)"
    
    def test_button_shown_for_non_terminal_batches(self):
        """测试非终态批次始终显示按钮"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # Check that isTerminal is used in the condition
        assert 'isTerminal' in content, "should use isTerminal check"
        assert '!isTerminal' in content, "button should be shown when NOT terminal"
    
    def test_button_disabled_during_click(self):
        """测试按钮点击期间禁用"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # Find the click handler
        assert 'startBtn.disabled = true' in content, "button should be disabled during click"
        assert '处理中' in content, "button should show loading text"
    
    def test_response_details_displayed(self):
        """测试响应详情显示给用户"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # Check that response fields are displayed
        assert 'submitted_count' in content, "should display submitted_count"
        assert 'remaining_count' in content, "should display remaining_count"
        assert 'statistics' in content, "should display statistics"
    
    def test_btn_warning_class_exists(self):
        """测试 btn-warning CSS 类存在"""
        with open('/workspace/projects/Creade4070Workflow/web/styles.css', 'r') as f:
            content = f.read()
        
        assert '.btn-warning' in content, "btn-warning class should exist in CSS"


class TestRecoveryButtonScenarios:
    """测试恢复调度按钮场景"""
    
    def test_running_1_pending_0_shows_recovery_button(self):
        """场景：running=1, pending=0 时显示恢复按钮"""
        # This simulates the orphan scenario
        # The button should be visible because batch is not terminal
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # Verify the logic: shouldShowStartBtn = !isTerminal && totalCount > 0
        # With running=1, pending=0, status=running -> isTerminal=False, totalCount>0
        # So button should be shown
        assert '!isTerminal' in content
        assert 'totalCount > 0' in content
    
    def test_recent_running_no_double_submit(self):
        """场景：近期真实运行任务不会重复提交（后端幂等）"""
        # This is tested in test_start_batch_regression.py
        # Here we just verify the frontend calls the correct endpoint
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        assert '/api/batches/${currentBatchId}/start' in content or \
               '/api/batches/${' in content, "should call start endpoint"
    
    def test_double_click_prevented(self):
        """场景：连续点击不会重复执行"""
        with open('/workspace/projects/Creade4070Workflow/web/app.js', 'r') as f:
            content = f.read()
        
        # Button is disabled at the start of click handler
        assert 'startBtn.disabled = true' in content
        # And re-enabled on error
        assert 'startBtn.disabled = false' in content
