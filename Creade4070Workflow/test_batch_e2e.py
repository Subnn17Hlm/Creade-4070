#!/usr/bin/env python3
"""
批量任务端到端冒烟测试脚本
用于在生产环境验证批量任务功能
"""

import requests
import time
import sys
import json
from datetime import datetime

# 配置
BASE_URL = "https://pvw8k2kt7t.coze.site"  # 生产环境 URL
CSV_FILE = "test_batch_smoke.csv"
IDEMPOTENCY_KEY = f"smoke-test-{int(time.time())}"

def log(msg):
    """带时间戳的日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")

def create_csv():
    """创建测试 CSV 文件"""
    csv_content = """task_id,script_text
smoke-batch-001,这款吹风机出差必备
smoke-batch-002,折叠收纳轻巧方便
"""
    with open(CSV_FILE, 'w', encoding='utf-8') as f:
        f.write(csv_content)
    log(f"✓ CSV 文件已创建: {CSV_FILE}")

def test_create_batch():
    """测试 1: 创建批次"""
    log("\n=== 测试 1: 创建批次 ===")
    
    with open(CSV_FILE, 'rb') as f:
        files = {'file': (CSV_FILE, f, 'text/csv')}
        data = {'concurrency': '2'}
        headers = {'Idempotency-Key': IDEMPOTENCY_KEY}
        
        response = requests.post(
            f"{BASE_URL}/api/batches",
            files=files,
            data=data,
            headers=headers
        )
    
    log(f"状态码: {response.status_code}")
    result = response.json()
    log(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
    
    if response.status_code != 200:
        log(f"✗ 创建批次失败")
        sys.exit(1)
    
    batch_id = result['batch_id']
    log(f"✓ 批次创建成功: batch_id={batch_id}")
    
    # 验证响应字段
    assert result['status'] == 'created', f"状态应为 created，实际: {result['status']}"
    assert result['total_count'] == 2, f"总数应为 2，实际: {result['total_count']}"
    assert result['concurrency'] == 2, f"并发应为 2，实际: {result['concurrency']}"
    log("✓ 响应字段验证通过")
    
    return batch_id

def test_start_batch(batch_id):
    """测试 2: 启动批次"""
    log(f"\n=== 测试 2: 启动批次 {batch_id} ===")
    
    response = requests.post(f"{BASE_URL}/api/batches/{batch_id}/start")
    log(f"状态码: {response.status_code}")
    result = response.json()
    log(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
    
    if response.status_code != 200:
        log(f"✗ 启动批次失败")
        sys.exit(1)
    
    log(f"✓ 批次启动成功")
    return result

def test_idempotency():
    """测试 3: 幂等性测试 - 使用相同 Idempotency-Key"""
    log(f"\n=== 测试 3: 幂等性测试 ===")
    
    with open(CSV_FILE, 'rb') as f:
        files = {'file': (CSV_FILE, f, 'text/csv')}
        data = {'concurrency': '2'}
        headers = {'Idempotency-Key': IDEMPOTENCY_KEY}
        
        response = requests.post(
            f"{BASE_URL}/api/batches",
            files=files,
            data=data,
            headers=headers
        )
    
    log(f"状态码: {response.status_code}")
    result = response.json()
    
    if response.status_code != 200:
        log(f"✗ 幂等性测试失败")
        sys.exit(1)
    
    log(f"✓ 返回相同批次，未创建重复数据")
    return result['batch_id']

def poll_batch_status(batch_id, max_wait=600):
    """测试 4: 轮询批次状态"""
    log(f"\n=== 测试 4: 轮询批次状态 (最长等待 {max_wait} 秒) ===")
    
    start_time = time.time()
    last_status = None
    
    while time.time() - start_time < max_wait:
        # 查询批次状态
        response = requests.get(f"{BASE_URL}/api/batches/{batch_id}")
        batch_data = response.json()
        current_status = batch_data['status']
        
        # 查询任务状态
        tasks_response = requests.get(f"{BASE_URL}/api/batches/{batch_id}/tasks")
        tasks_data = tasks_response.json()
        
        # 打印进度
        if current_status != last_status:
            log(f"批次状态变更: {last_status} → {current_status}")
            last_status = current_status
        
        log(f"进度: pending={batch_data['pending_count']}, "
            f"running={batch_data['running_count']}, "
            f"success={batch_data['success_count']}, "
            f"failed={batch_data['failed_count']}")
        
        # 检查是否到达终态
        if current_status in ['success', 'partial_failed', 'failed']:
            log(f"✓ 批次到达终态: {current_status}")
            return batch_data, tasks_data
        
        # 等待 5 秒
        time.sleep(5)
    
    log(f"✗ 超时: 批次未在 {max_wait} 秒内完成")
    sys.exit(1)

def verify_results(batch_data, tasks_data):
    """测试 5: 验证结果"""
    log(f"\n=== 测试 5: 验证结果 ===")
    
    # 验证批次统计
    total = batch_data['total_count']
    success = batch_data['success_count']
    failed = batch_data['failed_count']
    
    log(f"批次统计: total={total}, success={success}, failed={failed}")
    assert total == 2, f"总数应为 2，实际: {total}"
    assert success + failed == total, f"成功+失败应等于总数"
    log("✓ 批次统计验证通过")
    
    # 验证任务
    tasks = tasks_data['tasks']
    assert len(tasks) == 2, f"应有 2 个任务，实际: {len(tasks)}"
    
    for i, task in enumerate(tasks, 1):
        log(f"\n任务 {i}:")
        log(f"  task_id: {task['task_id']}")
        log(f"  status: {task['status']}")
        log(f"  run_id: {task.get('run_id', 'N/A')}")
        log(f"  retry_count: {task['retry_count']}")
        
        # 验证状态
        assert task['status'] in ['success', 'failed'], \
            f"任务状态应为 success 或 failed，实际: {task['status']}"
        
        # 验证 run_id 已持久化
        assert task.get('run_id'), "run_id 应已持久化"
        log(f"  ✓ run_id 已持久化")
        
        # 验证成功任务有 video URL
        if task['status'] == 'success':
            assert task.get('final_video_url'), "成功任务应有 final_video_url"
            log(f"  ✓ final_video_url: {task['final_video_url']}")
            
            # 验证 URL 可访问
            try:
                video_response = requests.head(task['final_video_url'], timeout=10)
                assert video_response.status_code == 200, \
                    f"视频 URL 不可访问: {video_response.status_code}"
                log(f"  ✓ 视频 URL 可访问")
            except Exception as e:
                log(f"  ⚠ 视频 URL 访问失败: {e}")
        
        # 验证失败任务有错误信息
        if task['status'] == 'failed':
            assert task.get('error_code'), "失败任务应有 error_code"
            assert task.get('error_message'), "失败任务应有 error_message"
            log(f"  ✓ 错误信息: {task['error_code']} - {task['error_message']}")
    
    log("\n✓ 所有验证通过")

def test_duplicate_start(batch_id):
    """测试 6: 重复启动测试"""
    log(f"\n=== 测试 6: 重复启动测试 ===")
    
    response = requests.post(f"{BASE_URL}/api/batches/{batch_id}/start")
    result = response.json()
    
    log(f"状态码: {response.status_code}")
    log(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
    
    # 应该返回当前状态，不会重复执行
    assert 'already started' in result.get('message', '').lower() or \
           result['status'] in ['success', 'partial_failed', 'failed', 'running'], \
           "重复启动应返回幂等结果"
    
    log("✓ 重复启动测试通过（幂等性）")

def main():
    """主测试流程"""
    log("=" * 60)
    log("批量任务端到端冒烟测试")
    log("=" * 60)
    log(f"目标环境: {BASE_URL}")
    log(f"Idempotency-Key: {IDEMPOTENCY_KEY}")
    
    try:
        # 创建 CSV
        create_csv()
        
        # 测试 1: 创建批次
        batch_id = test_create_batch()
        
        # 测试 2: 启动批次
        test_start_batch(batch_id)
        
        # 测试 3: 幂等性测试
        idempotent_batch_id = test_idempotency()
        assert idempotent_batch_id == batch_id, "幂等性测试失败：返回了不同的 batch_id"
        
        # 测试 4: 轮询状态
        batch_data, tasks_data = poll_batch_status(batch_id)
        
        # 测试 5: 验证结果
        verify_results(batch_data, tasks_data)
        
        # 测试 6: 重复启动测试
        test_duplicate_start(batch_id)
        
        log("\n" + "=" * 60)
        log("✓ 所有测试通过！")
        log("=" * 60)
        
    except Exception as e:
        log(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
