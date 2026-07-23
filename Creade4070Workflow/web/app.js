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

      const data = await res.json();

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
          const startData = await startRes.json();
          alert(`批次创建成功，但启动失败: ${startData.error || startData.detail || '未知错误'}`);
          showBatchMonitor(data.batch_id);
          startBatchPolling(data.batch_id);
        }
      } else {
        alert(`提交失败: ${data.error || data.detail || '未知错误'}`);
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
    startBtn.textContent = '启动中...';
    
    try {
      const res = await fetch(`/api/batches/${currentBatchId}/start`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ concurrency: 2 }),
      });
      
      const data = await res.json();
      
      if (res.ok) {
        alert('批次已启动');
        startBtn.style.display = 'none';
        loadBatchStatus(currentBatchId);
      } else {
        alert(`启动失败: ${data.error || data.detail || '未知错误'}`);
        startBtn.disabled = false;
        startBtn.textContent = '启动批次';
      }
    } catch (e) {
      alert(`请求失败: ${e.message}`);
      startBtn.disabled = false;
      startBtn.textContent = '启动批次';
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
    
    // 显示/隐藏启动按钮：基于任务状态，而不仅仅是批次状态
    // 如果存在 pending 任务且没有 running 任务，显示启动按钮
    const startBtn = document.getElementById('batch-start-btn');
    const counts = data.task_counts || {};
    const hasPending = (counts.pending || 0) > 0;
    const hasRunning = (counts.running || 0) > 0;
    
    // 显示启动按钮的条件：
    // 1. 批次状态为 created 或 pending
    // 2. 或者存在 pending 任务且没有 running 任务
    if (data.status === 'created' || data.status === 'pending' || (hasPending && !hasRunning)) {
      startBtn.style.display = 'inline-block';
      startBtn.disabled = false;
      startBtn.textContent = '启动批次';
    } else {
      startBtn.style.display = 'none';
    }

    // 如果批次已完成，停止轮询
    if (['success', 'failed', 'cancelled'].includes(data.status)) {
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
    if (!res.ok) return;

    const data = await res.json();
    const tasks = data.tasks || [];

    tasksDiv.innerHTML = '';
    tasks.forEach(task => {
      const status = normalizeTaskStatus(task.status);
      const item = document.createElement('div');
      item.className = 'task-item';
      item.innerHTML = `
        <div class="task-info">
          <div class="task-id">${task.task_id}</div>
          <div class="task-text">${escapeHtml(task.script_text || '').substring(0, 50)}${(task.script_text || '').length > 50 ? '...' : ''}</div>
          <span class="task-status ${status}">${status}</span>
        </div>
        <div class="task-actions">
          ${task.final_video_url ? `<a href="${task.final_video_url}" target="_blank" class="btn btn-secondary">查看视频</a>` : ''}
          ${status === 'failed' ? `<button class="btn btn-secondary" onclick="retryTask('${batchId}', '${task.task_id}')">重试</button>` : ''}
        </div>
      `;
      tasksDiv.appendChild(item);
    });
  } catch (e) {
    console.error('Failed to load tasks:', e);
  }
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
function startBatchPolling(batchId) {
  stopBatchPolling(); // 先停止已有的轮询
  
  pollingTimer = setInterval(() => {
    loadBatchStatus(batchId);
  }, 3000); // 每 3 秒轮询一次
}

function stopBatchPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
}

// ============================================================
// 任务重试
// ============================================================
async function retryTask(batchId, taskId) {
  if (!confirm('确定要重试这个任务吗？')) return;

  // 禁用按钮防止重复点击
  const btn = event.target;
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '重试中...';

  try {
    const res = await fetch(`/api/batches/${batchId}/tasks/${taskId}/retry`, {
      method: 'POST',
    });

    const data = await res.json();

    if (res.ok || res.status === 202) {
      // 任务已进入队列，显示成功消息
      alert(data.message || '任务已进入执行队列，请稍后刷新查看结果');
      // 刷新批次状态
      loadBatchStatus(batchId);
    } else {
      alert(`重试失败: ${data.error || data.detail || '未知错误'}`);
      // 恢复按钮状态
      btn.disabled = false;
      btn.textContent = originalText;
    }
  } catch (e) {
    alert(`请求失败: ${e.message}`);
    // 恢复按钮状态
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
      alert('获取任务列表失败');
      return;
    }

    const data = await res.json();
    const tasks = data.tasks || [];

    // 构建 CSV
    const headers = ['task_id', 'script_id', 'script_text', 'status', 'final_video_url', 'error_message'];
    const lines = [headers.join(',')];

    tasks.forEach(task => {
      const line = headers.map(h => {
        const val = task[h] || '';
        if (val.includes(',') || val.includes('\n')) {
          return `"${val.replace(/"/g, '""')}"`;
        }
        return val;
      }).join(',');
      lines.push(line);
    });

    // 下载
    const csvContent = lines.join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `batch_${batchId}_results.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert(`导出失败: ${e.message}`);
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
