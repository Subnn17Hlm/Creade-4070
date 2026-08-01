/**
 * 批量视频生成管理前端
 * 
 * 功能：
 * 1. 单条/批量模式切换
 * 2. CSV 上传、预览、编辑、删除
 * 3. 批量提交和监控
 * 4. 状态轮询（submitted 显示 pending，单次失败保留旧状态）
 * 5. 防止重复提交
 * 6. localStorage 恢复
 * 7. 导出 CSV
 */

// ============================================================
// 全局状态
// ============================================================
let csvData = []; // CSV 解析后的数据
let currentBatchId = null;
let pollingTimer = null;
let pollingFailCount = 0;
let lastBatchStatus = null;

// ============================================================
// 初始化
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  initModeSwitcher();
  initSingleMode();
  initBatchMode();
  restoreFromLocalStorage();
});

// ============================================================
// 模式切换
// ============================================================
function initModeSwitcher() {
  const singleBtn = document.getElementById('mode-single');
  const batchBtn = document.getElementById('mode-batch');
  const singleMode = document.getElementById('single-mode');
  const batchMode = document.getElementById('batch-mode');

  singleBtn.addEventListener('click', () => {
    singleBtn.classList.add('active');
    batchBtn.classList.remove('active');
    singleMode.classList.add('active');
    batchMode.classList.remove('active');
  });

  batchBtn.addEventListener('click', () => {
    batchBtn.classList.add('active');
    singleBtn.classList.remove('active');
    batchMode.classList.add('active');
    singleMode.classList.remove('active');
  });
}

// ============================================================
// 单条模式
// ============================================================
function initSingleMode() {
  const form = document.getElementById('single-form');
  const submitBtn = document.getElementById('single-submit-btn');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const scriptId = document.getElementById('single-script-id').value.trim();
    const scriptText = document.getElementById('single-script-text').value.trim();

    if (!scriptText) {
      showResult('single-result', '<span class="fail">请输入文案内容</span>');
      return;
    }

    // 禁用按钮防止重复提交
    submitBtn.disabled = true;
    submitBtn.textContent = '提交中...';

    try {
      const payload = {
        script_source: 'manual',
        script_text: scriptText,
      };
      if (scriptId) {
        payload.script_id = scriptId;
      }

      const res = await fetch('/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();

      if (res.ok) {
        let html = '<span class="ok">任务提交成功</span>\n';
        html += `<span class="info">run_id:</span> ${data.data?.run_id || data.run_id || 'N/A'}\n`;
        
        if (data.data?.final_video_url || data.final_video_url) {
          const videoUrl = data.data?.final_video_url || data.final_video_url;
          html += `<span class="ok">final_video_url:</span> ${videoUrl}\n`;
          html += `<video controls src="${videoUrl}"></video>`;
        } else {
          html += '<span class="info">任务已提交，正在后台处理...</span>\n';
          html += `<span class="info">请使用批次监控查看进度</span>`;
        }

        showResult('single-result', html);
      } else {
        showResult('single-result', `<span class="fail">提交失败: ${data.error || data.detail || '未知错误'}</span>`);
      }
    } catch (e) {
      showResult('single-result', `<span class="fail">请求失败: ${e.message}</span>`);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = '提交任务';
    }
  });
}

// ============================================================
// 批量模式
// ============================================================
function initBatchMode() {
  initCsvUpload();
  initCsvActions();
  initBatchMonitor();
}

// ============================================================
// CSV 上传
// ============================================================
function initCsvUpload() {
  const uploadArea = document.getElementById('upload-area');
  const fileInput = document.getElementById('csv-file');

  uploadArea.addEventListener('click', () => fileInput.click());

  uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '#4a9eff';
  });

  uploadArea.addEventListener('dragleave', () => {
    uploadArea.style.borderColor = '#444';
  });

  uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '#444';
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.csv')) {
      handleCsvFile(file);
    }
  });

  fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
      handleCsvFile(file);
    }
  });
}

function handleCsvFile(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    const text = e.target.result;
    parseCsv(text);
  };
  reader.readAsText(file);
}

function parseCsv(text) {
  const lines = text.split('\n').map(l => l.trim()).filter(l => l);
  if (lines.length < 2) {
    alert('CSV 文件至少需要包含标题行和一行数据');
    return;
  }

  const headers = lines[0].split(',').map(h => h.trim());
  const scriptTextIdx = headers.indexOf('script_text');
  const scriptIdIdx = headers.indexOf('script_id');
  const titleIdx = headers.indexOf('title');

  if (scriptTextIdx === -1) {
    alert('CSV 必须包含 script_text 列');
    return;
  }

  csvData = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(',').map(c => c.trim());
    const scriptText = cols[scriptTextIdx] || '';
    if (!scriptText) continue;

    csvData.push({
      script_id: scriptIdIdx !== -1 ? cols[scriptIdIdx] || '' : '',
      script_text: scriptText,
      title: titleIdx !== -1 ? cols[titleIdx] || '' : '',
    });
  }

  if (csvData.length === 0) {
    alert('CSV 中没有有效的数据行');
    return;
  }

  renderCsvPreview();
}

function renderCsvPreview() {
  const preview = document.getElementById('csv-preview');
  const tbody = document.getElementById('csv-tbody');
  const count = document.getElementById('csv-count');

  count.textContent = csvData.length;
  tbody.innerHTML = '';

  csvData.forEach((row, idx) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td><input type="text" value="${escapeHtml(row.script_id)}" data-idx="${idx}" data-field="script_id"></td>
      <td><textarea data-idx="${idx}" data-field="script_text">${escapeHtml(row.script_text)}</textarea></td>
      <td><button class="btn btn-secondary" onclick="removeCsvRow(${idx})">删除</button></td>
    `;
    tbody.appendChild(tr);
  });

  // 绑定编辑事件
  tbody.querySelectorAll('input, textarea').forEach(el => {
    el.addEventListener('change', (e) => {
      const idx = parseInt(e.target.dataset.idx);
      const field = e.target.dataset.field;
      csvData[idx][field] = e.target.value;
    });
  });

  preview.style.display = 'block';
}

function removeCsvRow(idx) {
  csvData.splice(idx, 1);
  renderCsvPreview();
}

// ============================================================
// CSV 操作
// ============================================================
function initCsvActions() {
  const clearBtn = document.getElementById('csv-clear-btn');
  const submitBtn = document.getElementById('csv-submit-btn');

  clearBtn.addEventListener('click', () => {
    csvData = [];
    document.getElementById('csv-preview').style.display = 'none';
    document.getElementById('csv-file').value = '';
  });

  submitBtn.addEventListener('click', async () => {
    if (csvData.length === 0) {
      alert('请先上传 CSV 文件');
      return;
    }

    // 禁用按钮防止重复提交
    submitBtn.disabled = true;
    submitBtn.textContent = '提交中...';

    try {
      // 构建 CSV 内容
      const csvContent = buildCsvContent(csvData);
      const blob = new Blob([csvContent], { type: 'text/csv' });
      const formData = new FormData();
      formData.append('file', blob, 'batch.csv');
      formData.append('concurrency', document.getElementById('concurrency').value);

      const res = await fetch('/api/batches', {
        method: 'POST',
        body: formData,
      });

      // Safely parse response - handle both JSON and non-JSON responses
      let data;
      const contentType = res.headers.get('content-type') || '';
      if (contentType.includes('application/json')) {
        data = await res.json();
      } else {
        const text = await res.text();
        data = { error: text || `HTTP ${res.status}` };
      }

      if (res.ok) {
        currentBatchId = data.batch_id;
        saveToLocalStorage(data.batch_id);
        
        // 自动启动批次
        const startRes = await fetch(`/api/batches/${data.batch_id}/start`, {
          method: 'POST',
        });
        
        if (startRes.ok) {
          showBatchMonitor(data.batch_id);
          startBatchPolling(data.batch_id);
        } else {
          const startContentType = startRes.headers.get('content-type') || '';
          let startData;
          if (startContentType.includes('application/json')) {
            startData = await startRes.json();
          } else {
            const startText = await startRes.text();
            startData = { error: startText || `HTTP ${startRes.status}` };
          }
          const errMsg = startData.detail?.error_message || startData.error || startData.detail || '未知错误';
          alert(`批次创建成功，但启动失败: ${errMsg}`);
          showBatchMonitor(data.batch_id);
          startBatchPolling(data.batch_id);
        }
      } else {
        const errMsg = data.detail?.error_message || data.error || data.detail || `HTTP ${res.status}`;
        alert(`提交失败: ${errMsg}`);
      }
    } catch (e) {
      alert(`请求失败: ${e.message}`);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = '提交批次';
    }
  });
}

function buildCsvContent(rows) {
  const headers = ['script_id', 'script_text', 'title'];
  const lines = [headers.join(',')];
  
  rows.forEach(row => {
    const line = headers.map(h => {
      const val = row[h] || '';
      // 如果包含逗号或换行，用引号包裹
      if (val.includes(',') || val.includes('\n')) {
        return `"${val.replace(/"/g, '""')}"`;
      }
      return val;
    }).join(',');
    lines.push(line);
  });

  return lines.join('\n');
}

// ============================================================
// 批次监控
// ============================================================
function initBatchMonitor() {
  const refreshBtn = document.getElementById('batch-refresh-btn');
  const exportBtn = document.getElementById('batch-export-btn');
  const startBtn = document.getElementById('batch-start-btn');

  refreshBtn.addEventListener('click', () => {
    if (currentBatchId) {
      loadBatchStatus(currentBatchId);
    }
  });

  exportBtn.addEventListener('click', () => {
    if (currentBatchId) {
      exportBatchCsv(currentBatchId);
    }
  });

  startBtn.addEventListener('click', async () => {
    if (!currentBatchId) return;
    
    // 禁用按钮防止重复点击
    startBtn.disabled = true;
    const originalText = startBtn.textContent;
    startBtn.textContent = '处理中…';
    
    try {
      const res = await fetch(`/api/batches/${currentBatchId}/start`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ concurrency: 2 }),
      });
      
      // HTTP 200 and 202 both mean success
      if (res.ok || res.status === 202) {
        let data = {};
        try {
          data = await res.json();
        } catch (_) {
          // Empty response body is OK
        }
        
        // Build detailed message from response
        const parts = [];
        if (data.message) parts.push(data.message);
        if (data.selected_count !== undefined) parts.push(`选择: ${data.selected_count}`);
        if (data.submitted_count !== undefined) parts.push(`提交: ${data.submitted_count}`);
        if (data.native_async_count > 0) parts.push(`native: ${data.native_async_count}`);
        if (data.fallback_count > 0) parts.push(`fallback: ${data.fallback_count}`);
        if (data.remaining_count !== undefined && data.remaining_count > 0) parts.push(`剩余: ${data.remaining_count}`);
        
        if (data.statistics) {
          const s = data.statistics;
          parts.push(`[pending=${s.pending||0}, running=${s.running||0}, success=${s.success||0}, failed=${s.failed||0}]`);
        }
        
        const msg = parts.length > 0 ? parts.join(' | ') : '操作完成';
        showStatusMessage(msg, 'success');
        
        // Don't hide button - let the polling update decide
        loadBatchStatus(currentBatchId);
      } else {
        // Extract real error from response
        let errorMsg = `HTTP ${res.status}`;
        try {
          const errData = await res.json();
          const detail = errData.detail || errData;
          errorMsg = detail.error || detail.message || errData.error || errData.message || errorMsg;
        } catch (_) {}
        
        showStatusMessage(`操作失败: ${errorMsg}`, 'error');
        startBtn.disabled = false;
        startBtn.textContent = originalText;
      }
    } catch (e) {
      // Network error or timeout - check if batch was actually started
      showStatusMessage(`请求异常: ${e.message}，正在检查状态…`, 'warning');
      
      try {
        const checkRes = await fetch(`/api/batches/${currentBatchId}`);
        if (checkRes.ok) {
          const checkData = await checkRes.json();
          // If batch is already running or has running/queued tasks, it was started
          if (['running', 'success', 'failed', 'partial_failed'].includes(checkData.status)) {
            showStatusMessage('批次已在运行中', 'success');
            loadBatchStatus(currentBatchId);
            return;
          }
        }
      } catch (_) {}
      
      startBtn.disabled = false;
      startBtn.textContent = originalText;
    }
  });
}

function showBatchMonitor(batchId) {
  document.getElementById('batch-monitor').style.display = 'block';
  document.getElementById('csv-preview').style.display = 'none';
}

async function loadBatchStatus(batchId) {
  try {
    const res = await fetch(`/api/batches/${batchId}`);
    
    if (!res.ok) {
      pollingFailCount++;
      if (pollingFailCount <= 3) {
        // 前 3 次失败保留旧状态，显示重试提示
        showStatusMessage('状态更新暂时失败，正在重试...', 'warning');
        return;
      } else {
        // 超过 3 次失败，显示错误
        showStatusMessage(`状态查询失败: HTTP ${res.status}`, 'error');
        return;
      }
    }

    // 请求成功，重置失败计数
    pollingFailCount = 0;
    const data = await res.json();
    lastBatchStatus = data;

    renderBatchSummary(data);
    await renderBatchTasks(batchId);
    
    // 显示/隐藏启动/恢复按钮：对未终态批次始终保留入口
    const startBtn = document.getElementById('batch-start-btn');
    const counts = data.task_counts || {};
    const totalCount = data.total_count || 0;
    // waiting 包含 created、pending、queued
    const waitingCount = (counts.pending || 0) + (counts.queued || 0);
    const runningCount = counts.running || 0;
    const successCount = counts.success || 0;
    const failedCount = counts.failed || 0;
    const isTerminal = ['success', 'failed', 'cancelled', 'partial_failed'].includes(data.status);
    
    // 显示按钮的条件：批次未终态 且 总数 > 0
    // 不再因 running > 0 就隐藏，因为可能有孤儿任务需要恢复
    const shouldShowStartBtn = !isTerminal && totalCount > 0;
    
    if (shouldShowStartBtn) {
      startBtn.style.display = 'inline-block';
      startBtn.disabled = false;
      
      // 根据状态设置按钮文本
      if (runningCount > 0 && waitingCount === 0) {
        // 有运行中任务但无等待任务 - 可能是孤儿任务，显示恢复入口
        startBtn.textContent = '检查/恢复调度';
        startBtn.className = 'btn btn-warning';
      } else if (runningCount > 0 && waitingCount > 0) {
        // 有运行中任务且有等待任务 - 可能是补位或恢复
        startBtn.textContent = '检查/恢复调度';
        startBtn.className = 'btn btn-warning';
      } else if (waitingCount > 0 && runningCount === 0) {
        // 有等待任务且无运行任务 - 正常启动
        startBtn.textContent = '启动批次';
        startBtn.className = 'btn btn-primary';
      } else {
        // 其他情况
        startBtn.textContent = '检查/恢复调度';
        startBtn.className = 'btn btn-warning';
      }
    } else {
      startBtn.style.display = 'none';
    }

    // 如果批次已完成或无活动任务，停止轮询
    if (isBatchTerminal(data)) {
      stopBatchPolling();
    }
  } catch (e) {
    pollingFailCount++;
    if (pollingFailCount <= 3) {
      showStatusMessage('状态更新暂时失败，正在重试...', 'warning');
    } else {
      showStatusMessage(`状态查询异常: ${e.message}`, 'error');
    }
  }
}

function renderBatchSummary(data) {
  const summary = document.getElementById('batch-summary');
  const counts = data.task_counts || {};

  summary.innerHTML = `
    <div class="summary-item total">
      <div class="label">总数</div>
      <div class="value">${data.total_count || 0}</div>
    </div>
    <div class="summary-item pending">
      <div class="label">等待</div>
      <div class="value">${counts.pending || 0}</div>
    </div>
    <div class="summary-item running">
      <div class="label">运行</div>
      <div class="value">${counts.running || 0}</div>
    </div>
    <div class="summary-item success">
      <div class="label">成功</div>
      <div class="value">${counts.success || 0}</div>
    </div>
    <div class="summary-item warning">
      <div class="label">警告</div>
      <div class="value">${counts.warning || 0}</div>
    </div>
    <div class="summary-item failed">
      <div class="label">失败</div>
      <div class="value">${counts.failed || 0}</div>
    </div>
  `;
}

async function renderBatchTasks(batchId) {
  const tasksDiv = document.getElementById('batch-tasks');

  try {
    const res = await fetch(`/api/batches/${batchId}/tasks`);
    
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}`;
      try {
        const errData = await res.json();
        errorMsg = errData.detail?.error || errData.detail?.message || errData.error || errData.message || errorMsg;
      } catch (_) {}
      tasksDiv.innerHTML = `<div class="task-error">获取任务列表失败: ${escapeHtml(errorMsg)}</div>`;
      return;
    }

    const data = await res.json();
    console.log('[BatchPage] 任务列表响应:', { status: res.status, keys: Object.keys(data), tasksCount: Array.isArray(data.tasks) ? data.tasks.length : 'not array' });
    
    // Support multiple response structures: { tasks: [] }, { data: { tasks: [] } }, or direct array
    let tasks;
    if (Array.isArray(data)) {
      tasks = data;
    } else if (data && Array.isArray(data.tasks)) {
      tasks = data.tasks;
    } else if (data && data.data && Array.isArray(data.data.tasks)) {
      tasks = data.data.tasks;
    } else {
      console.error('Unexpected tasks response structure:', JSON.stringify(data).substring(0, 500));
      tasksDiv.innerHTML = `<div class="task-error">任务列表响应格式异常，请检查控制台</div>`;
      return;
    }

    tasksDiv.innerHTML = '';
    
    if (tasks.length === 0) {
      // Check if statistics show tasks exist but tasks array is empty
      const statsEl = document.getElementById('batch-summary');
      const totalFromStats = statsEl ? (parseInt(statsEl.querySelector('.summary-item.total .value')?.textContent || '0') || 0) : 0;
      if (totalFromStats > 0) {
        tasksDiv.innerHTML = `<div class="task-error">统计显示 ${totalFromStats} 条任务，但任务明细未返回。请检查控制台日志。</div>`;
        console.error('[BatchPage] 统计与任务明细不一致: total_count=' + totalFromStats + ', tasks.length=0, response keys:', Object.keys(data));
      } else {
        tasksDiv.innerHTML = `<div class="task-empty">暂无任务数据</div>`;
      }
      return;
    }

    tasks.forEach(task => {
      const status = normalizeTaskStatus(task.status);
      const item = document.createElement('div');
      item.className = `task-item task-status-${status}`;
      item.dataset.taskId = task.task_id;
      
      // Build task card content
      let cardHtml = `
        <div class="task-header">
          <span class="task-id" title="${escapeHtml(task.task_id || '')}">${escapeHtml((task.task_id || '').substring(0, 8))}...</span>
          <span class="task-status-badge ${status}">${statusLabel(status)}</span>
          ${task.retry_count > 0 ? `<span class="task-retry-count">重试 ${task.retry_count} 次</span>` : ''}
        </div>
      `;
      
      // Script info
      if (task.script_id) {
        cardHtml += `<div class="task-field"><span class="label">脚本ID:</span> ${escapeHtml(task.script_id)}</div>`;
      }
      if (task.title) {
        cardHtml += `<div class="task-field"><span class="label">标题:</span> ${escapeHtml(task.title)}</div>`;
      }
      if (task.script_text) {
        const snippet = task.script_text.length > 80 ? task.script_text.substring(0, 80) + '...' : task.script_text;
        cardHtml += `<div class="task-field"><span class="label">脚本:</span> ${escapeHtml(snippet)}</div>`;
      }
      
      // Warning
      if (task.warning) {
        cardHtml += `<div class="task-field task-warning"><span class="label">警告:</span> ${escapeHtml(task.warning)}</div>`;
      }
      
      // Error info
      if (task.error_code) {
        cardHtml += `<div class="task-field task-error-code"><span class="label">错误码:</span> ${escapeHtml(task.error_code)}</div>`;
      }
      if (task.error_message) {
        cardHtml += `<div class="task-field task-error-msg"><span class="label">错误:</span> ${escapeHtml(task.error_message)}</div>`;
      }
      
      // Actions
      let actionsHtml = '<div class="task-actions">';
      
      if (status === 'success') {
        if (task.final_video_url) {
          actionsHtml += `<a href="${escapeHtml(task.final_video_url)}" target="_blank" rel="noopener" class="btn btn-success">查看视频</a>`;
        } else {
          actionsHtml += `<span class="task-note">任务成功，但视频地址尚未回写</span>`;
        }
      }
      
      if (status === 'failed') {
        actionsHtml += `<button class="btn btn-warning task-retry-btn" data-batch-id="${escapeHtml(batchId)}" data-task-id="${escapeHtml(task.task_id)}">重试</button>`;
      }
      
      actionsHtml += '</div>';
      
      item.innerHTML = cardHtml + actionsHtml;
      tasksDiv.appendChild(item);
    });
    
    // Attach retry button handlers
    tasksDiv.querySelectorAll('.task-retry-btn').forEach(btn => {
      btn.addEventListener('click', () => retryTask(btn.dataset.batchId, btn.dataset.taskId, btn));
    });
    
  } catch (e) {
    console.error('Failed to load tasks:', e);
    tasksDiv.innerHTML = `<div class="task-error">加载任务列表异常: ${escapeHtml(e.message)}</div>`;
  }
}

function statusLabel(status) {
  const labels = {
    'created': '等待',
    'pending': '等待',
    'queued': '等待',
    'running': '运行中',
    'success': '成功',
    'failed': '失败',
    'timeout': '超时',
  };
  return labels[status] || status;
}

/**
 * 规范化任务状态
 * - submitted/queued → pending
 * - 只有后端明确返回 timeout 才显示 timeout
 */
function normalizeTaskStatus(status) {
  if (!status) return 'pending';
  
  const s = status.toLowerCase();
  
  // submitted 和 queued 都显示为 pending
  if (s === 'submitted' || s === 'queued') {
    return 'pending';
  }
  
  // 只有后端明确返回 timeout 才显示 timeout
  if (s === 'timeout') {
    return 'timeout';
  }
  
  // 其他状态直接返回
  return s;
}

// ============================================================
// 轮询控制
// ============================================================
let visibilityHandler = null;
let beforeUnloadHandler = null;

function startBatchPolling(batchId) {
  stopBatchPolling(); // 先停止已有的轮询，防止重复创建
  
  pollingTimer = setInterval(() => {
    loadBatchStatus(batchId);
  }, 3000); // 每 3 秒轮询一次
  
  // 页面可见性变化处理：隐藏时暂停，可见时立即刷新
  if (visibilityHandler) {
    document.removeEventListener('visibilitychange', visibilityHandler);
  }
  visibilityHandler = () => {
    if (document.hidden) {
      // 页面隐藏时停止轮询
      if (pollingTimer) {
        clearInterval(pollingTimer);
        pollingTimer = null;
      }
    } else {
      // 页面重新可见时立即刷新一次
      loadBatchStatus(batchId);
      // 如果还需要轮询（有 pending/running 任务），重新启动
      const counts = lastBatchStatus?.task_counts || {};
      const pendingCount = (counts.pending || 0) + (counts.queued || 0);
      const runningCount = counts.running || 0;
      if (pendingCount > 0 || runningCount > 0) {
        startBatchPolling(batchId);
      }
    }
  };
  document.addEventListener('visibilitychange', visibilityHandler);
  
  // 页面卸载时清理定时器
  if (beforeUnloadHandler) {
    window.removeEventListener('beforeunload', beforeUnloadHandler);
  }
  beforeUnloadHandler = () => {
    stopBatchPolling();
  };
  window.addEventListener('beforeunload', beforeUnloadHandler);
}

function stopBatchPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
  // 清理事件监听器
  if (visibilityHandler) {
    document.removeEventListener('visibilitychange', visibilityHandler);
    visibilityHandler = null;
  }
  if (beforeUnloadHandler) {
    window.removeEventListener('beforeunload', beforeUnloadHandler);
    beforeUnloadHandler = null;
  }
}

// 判断批次是否处于终止状态（无需继续轮询）
function isBatchTerminal(data) {
  // 批次状态为终态
  if (['success', 'failed', 'cancelled', 'partial_failed'].includes(data.status)) {
    return true;
  }
  // 没有 pending/running 任务（使用 task_counts 或 lastTaskList 作为兜底）
  let counts = data.task_counts || {};
  // 如果 task_counts 不可用，使用 lastTaskList 的 statistics 作为兜底
  if (!data.task_counts && lastTaskList && lastTaskList.statistics) {
    counts = {
      pending: lastTaskList.statistics.pending_count || 0,
      queued: lastTaskList.statistics.queued_count || 0,
      running: lastTaskList.statistics.running_count || 0
    };
  }
  const pendingCount = (counts.pending || 0) + (counts.queued || 0);
  const runningCount = counts.running || 0;
  if (pendingCount === 0 && runningCount === 0) {
    return true;
  }
  return false;
}

// ============================================================
// 任务重试
// ============================================================
async function retryTask(batchId, taskId, btnElement) {
  // btnElement is the button that was clicked; if not provided, find it
  const btn = btnElement || document.querySelector(`.task-retry-btn[data-task-id="${taskId}"]`);
  if (!btn || btn.disabled) return;

  // Disable button to prevent duplicate clicks
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '重试中…';

  try {
    const res = await fetch(`/api/batches/${batchId}/tasks/${taskId}/retry`, {
      method: 'POST',
    });

    // HTTP 200 and 202 both mean success
    if (res.ok || res.status === 202) {
      let msg = '任务已进入执行队列';
      try {
        const data = await res.json();
        msg = data.message || data.msg || msg;
      } catch (_) {
        // Empty response body is OK
      }
      showStatusMessage(msg, 'success');
      // Update the task card status to queued/pending immediately
      const taskItem = btn.closest('.task-item');
      if (taskItem) {
        const badge = taskItem.querySelector('.task-status-badge');
        if (badge) {
          badge.className = 'task-status-badge queued';
          badge.textContent = '等待';
        }
        // Remove retry button
        btn.remove();
      }
      // Refresh batch status after a short delay
      setTimeout(() => loadBatchStatus(batchId), 1000);
    } else {
      // Extract real error from response
      let errorMsg = `HTTP ${res.status}`;
      try {
        const errData = await res.json();
        const detail = errData.detail || errData;
        errorMsg = detail.error || detail.message || errData.error || errData.message || errorMsg;
      } catch (_) {}
      
      showStatusMessage(`重试失败: ${errorMsg}`, 'error');
      // Restore button
      btn.disabled = false;
      btn.textContent = originalText;
    }
  } catch (e) {
    // Network error or timeout - check if task was actually submitted
    showStatusMessage(`请求异常: ${e.message}，正在检查任务状态…`, 'warning');
    
    try {
      // Re-fetch task status to check if it was actually submitted
      const checkRes = await fetch(`/api/batches/${batchId}/tasks`);
      if (checkRes.ok) {
        const checkData = await checkRes.json();
        const tasks = checkData.tasks || [];
        const task = tasks.find(t => t.task_id === taskId);
        if (task && ['queued', 'running'].includes(task.status)) {
          showStatusMessage('任务已进入队列', 'success');
          // Update UI
          const taskItem = btn.closest('.task-item');
          if (taskItem) {
            const badge = taskItem.querySelector('.task-status-badge');
            if (badge) {
              badge.className = 'task-status-badge queued';
              badge.textContent = '等待';
            }
            btn.remove();
          }
          setTimeout(() => loadBatchStatus(batchId), 1000);
          return;
        }
      }
    } catch (_) {}
    
    // Restore button
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

// ============================================================
// 导出 CSV
// ============================================================
async function exportBatchCsv(batchId) {
  try {
    const res = await fetch(`/api/batches/${batchId}/tasks`);
    
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}`;
      try {
        const errData = await res.json();
        errorMsg = errData.detail?.error || errData.error || errData.message || errorMsg;
      } catch (_) {}
      showStatusMessage(`导出失败: ${errorMsg}`, 'error');
      return;
    }

    const data = await res.json();
    
    // Support multiple response structures
    let tasks;
    if (Array.isArray(data)) {
      tasks = data;
    } else if (data && Array.isArray(data.tasks)) {
      tasks = data.tasks;
    } else if (data && data.data && Array.isArray(data.data.tasks)) {
      tasks = data.data.tasks;
    } else {
      showStatusMessage('导出失败: 任务列表响应格式异常', 'error');
      return;
    }

    // CSV headers - full fields
    const headers = [
      'task_id', 'batch_id', 'script_id', 'title', 'script_text',
      'status', 'final_video_url', 'warning', 'error_code', 'error_message',
      'retry_count', 'run_id', 'async_task_id',
      'created_at', 'started_at', 'completed_at', 'updated_at'
    ];
    
    // Build CSV with proper escaping
    const csvEscape = (val) => {
      if (val === null || val === undefined) return '';
      const str = String(val);
      // Always wrap in quotes if contains comma, double-quote, newline, or CJK
      if (str.includes(',') || str.includes('"') || str.includes('\n') || str.includes('\r')) {
        return `"${str.replace(/"/g, '""')}"`;
      }
      return str;
    };
    
    const lines = [headers.join(',')];
    
    tasks.forEach(task => {
      const row = headers.map(h => {
        let val = task[h];
        // Handle nested objects
        if (val !== null && val !== undefined && typeof val === 'object') {
          val = JSON.stringify(val);
        }
        return csvEscape(val);
      });
      lines.push(row.join(','));
    });

    // UTF-8 BOM for Excel compatibility
    const csvContent = '\uFEFF' + lines.join('\r\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const now = new Date().toISOString().replace(/[:.]/g, '-').substring(0, 19);
    a.download = `batch_${batchId}_${now}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    
    showStatusMessage(`已导出 ${tasks.length} 条任务`, 'success');
  } catch (e) {
    showStatusMessage(`导出异常: ${e.message}`, 'error');
  }
}

// ============================================================
// localStorage 持久化
// ============================================================
function saveToLocalStorage(batchId) {
  localStorage.setItem('batch_monitor_batch_id', batchId);
}

function restoreFromLocalStorage() {
  const batchId = localStorage.getItem('batch_monitor_batch_id');
  if (batchId) {
    currentBatchId = batchId;
    showBatchMonitor(batchId);
    loadBatchStatus(batchId);
    startBatchPolling(batchId);
  }
}

// ============================================================
// 辅助函数
// ============================================================
function showResult(elementId, html) {
  const el = document.getElementById(elementId);
  el.innerHTML = html;
  el.style.display = 'block';
}

function showStatusMessage(message, type) {
  const monitor = document.getElementById('batch-monitor');
  let msgEl = monitor.querySelector('.status-message');
  
  if (!msgEl) {
    msgEl = document.createElement('div');
    msgEl.className = 'status-message';
    monitor.insertBefore(msgEl, monitor.firstChild);
  }
  
  msgEl.className = `status-message ${type}`;
  msgEl.textContent = message;
  msgEl.style.display = 'block';
  
  // 3 秒后隐藏
  setTimeout(() => {
    msgEl.style.display = 'none';
  }, 3000);
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
