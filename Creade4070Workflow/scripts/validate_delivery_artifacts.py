#!/usr/bin/env python3
"""
交付物验证脚本 - validate_delivery_artifacts.py
验证所有JSON文件的完整性和编码，以及原始文案一致性。
"""

import json
import os
import hashlib
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Tuple


def atomic_json_write(file_path: str, data: Any) -> None:
    directory = os.path.dirname(file_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".json-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        with open(temp_path, "r", encoding="utf-8") as handle:
            json.load(handle)
        os.replace(temp_path, file_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def validate_json_file(file_path: str) -> Dict[str, Any]:
    """验证单个JSON文件的完整性和编码"""
    result = {
        "file_path": file_path,
        "valid": False,
        "encoding": "unknown",
        "parse_error": None,
        "file_size": 0,
    }
    
    if not os.path.exists(file_path):
        result["parse_error"] = "file_not_found"
        return result
    
    result["file_size"] = os.path.getsize(file_path)
    
    # 尝试检测编码
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
        # 检查是否有BOM
        if raw.startswith(b"\xef\xbb\xbf"):
            result["encoding"] = "utf-8-bom"
            raw = raw[3:]
        else:
            result["encoding"] = "utf-8"
        
        # 尝试解析为UTF-8
        text = raw.decode("utf-8")
        # 尝试解析JSON
        json.loads(text)
        result["valid"] = True
    except UnicodeDecodeError as e:
        result["parse_error"] = f"encoding_error: {str(e)}"
    except json.JSONDecodeError as e:
        result["parse_error"] = f"json_parse_error: {str(e)}"
    except Exception as e:
        result["parse_error"] = f"unknown_error: {str(e)}"
    
    return result


def validate_directory(dir_path: str) -> List[Dict[str, Any]]:
    """递归验证目录下所有JSON文件"""
    results = []
    
    if not os.path.exists(dir_path):
        return results
    
    for root, dirs, files in os.walk(dir_path):
        for fname in files:
            if fname.endswith(".json"):
                fpath = os.path.join(root, fname)
                results.append(validate_json_file(fpath))
    
    return results


def compute_sha256(file_path: str) -> str:
    """计算文件的SHA256"""
    if not os.path.exists(file_path):
        return ""
    with open(file_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def verify_script_consistency(run_dir: str, batch_dir: str) -> Dict[str, Any]:
    """验证原始文案一致性
    
    只验证包含新格式input_meta.json（含original_script_sha256）的run
    """
    result = {
        "run_dir": run_dir,
        "batch_dir": batch_dir,
        "consistent": False,
        "run_sha256": "",
        "batch_sha256": "",
        "expected_sha256": "",
        "matches_input_meta": False,
        "first_diff_pos": -1,
        "error": None,
        "skipped": False,
        "skip_reason": ""
    }
    
    run_script = os.path.join(run_dir, "original_script.txt")
    batch_script = os.path.join(batch_dir, "original_script.txt")
    input_meta = os.path.join(run_dir, "input_meta.json")
    
    # 检查是否有新格式的input_meta.json（含original_script_sha256）
    if not os.path.exists(input_meta):
        result["skipped"] = True
        result["skip_reason"] = "input_meta.json不存在"
        return result
    
    with open(input_meta, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    
    if "original_script_sha256" not in meta:
        result["skipped"] = True
        result["skip_reason"] = "legacy_run_no_sha256"
        return result
    
    if not os.path.exists(run_script):
        result["error"] = "run_original_script_not_found"
        return result
    
    if not os.path.exists(batch_script):
        result["error"] = "batch_original_script_not_found"
        return result
    
    with open(run_script, "rb") as f:
        run_content = f.read()
    with open(batch_script, "rb") as f:
        batch_content = f.read()
    
    result["run_sha256"] = hashlib.sha256(run_content).hexdigest()
    result["batch_sha256"] = hashlib.sha256(batch_content).hexdigest()
    result["expected_sha256"] = str(meta["original_script_sha256"]).lower()
    result["matches_input_meta"] = (
        result["run_sha256"].lower() == result["expected_sha256"]
    )
    
    if run_content == batch_content and result["matches_input_meta"]:
        result["consistent"] = True
    else:
        # 找到第一个差异位置
        min_len = min(len(run_content), len(batch_content))
        for i in range(min_len):
            if run_content[i] != batch_content[i]:
                result["first_diff_pos"] = i
                break
        if result["first_diff_pos"] == -1 and len(run_content) != len(batch_content):
            result["first_diff_pos"] = min_len
    
    return result


def main():
    """主函数"""
    project_root = os.environ.get("COZE_WORKSPACE_PATH", "/workspace/projects")
    
    # 验证目录列表
    dirs_to_validate = [
        os.path.join(project_root, "素材质量优化"),
        os.path.join(project_root, "runs", "material_quality_smoke_04"),
        os.path.join(project_root, "runs", "material_quality_fix_01"),
        os.path.join(project_root, "runs", "material_quality_fix_02"),
        os.path.join(project_root, "runs", "material_quality_fix_03"),
        os.path.join(project_root, "runs", "material_quality_fix_04"),
        os.path.join(project_root, "runs", "material_quality_fix_05"),
    ]
    
    all_results = []
    all_valid = True
    
    print("=" * 80)
    print("交付物验证报告")
    print("=" * 80)
    
    # 1. 验证JSON文件
    print("\n## 1. JSON文件完整性验证\n")
    for dir_path in dirs_to_validate:
        if os.path.exists(dir_path):
            results = validate_directory(dir_path)
            all_results.extend(results)
            for r in results:
                status = "PASS" if r["valid"] else "FAIL"
                print(f"  {status}: {r['file_path']}")
                print(f"         size={r['file_size']}, encoding={r['encoding']}")
                if r["parse_error"]:
                    print(f"         error: {r['parse_error']}")
                    all_valid = False
    
    # 2. 验证原始文案一致性
    print("\n## 2. 原始文案一致性验证\n")
    skipped_count = 0
    for i in ["01", "02", "03", "04", "05"]:
        run_dir = os.path.join(project_root, "runs", f"material_quality_fix_{i}")
        batch_dir = os.path.join(project_root, "runs", f"batch_fix2_{i}")
        
        if os.path.exists(run_dir) and os.path.exists(batch_dir):
            consistency = verify_script_consistency(run_dir, batch_dir)
            if consistency.get("skipped"):
                status = "SKIP"
                skipped_count += 1
                print(f"  {status}: material_quality_fix_{i} vs batch_fix2_{i}")
                print(f"         reason: {consistency.get('skip_reason', 'unknown')}")
            else:
                status = "PASS" if consistency.get("consistent") else "FAIL"
                print(f"  {status}: material_quality_fix_{i} vs batch_fix2_{i}")
                print(f"         run_sha256:    {consistency.get('run_sha256', 'N/A')[:16]}...")
                print(f"         batch_sha256:  {consistency.get('batch_sha256', 'N/A')[:16]}...")
                print(f"         expected_sha256: {consistency.get('expected_sha256', 'N/A')[:16]}...")
                print(f"         matches_input_meta: {consistency.get('matches_input_meta', False)}")
                if not consistency.get("consistent"):
                    print(f"         first_diff_pos: {consistency.get('first_diff_pos', -1)}")
                    if consistency.get("error"):
                        print(f"         error: {consistency['error']}")
                    all_valid = False
    
    # 3. 汇总
    print("\n" + "=" * 80)
    json_count = len(all_results)
    valid_count = sum(1 for r in all_results if r["valid"])
    invalid_count = json_count - valid_count
    
    print(f"JSON文件总数: {json_count}")
    print(f"有效: {valid_count}")
    print(f"无效: {invalid_count}")
    print(f"\n最终结论: {'ALL PASS' if all_valid else 'VALIDATION FAILED'}")
    print("=" * 80)
    
    # 输出JSON格式的结果
    output = {
        "json_validation": all_results,
        "all_valid": all_valid,
        "summary": {
            "total_json_files": json_count,
            "valid_count": valid_count,
            "invalid_count": invalid_count,
        }
    }
    
    # 保存结果
    output_path = os.path.join(project_root, "素材质量优化", "delivery_validation_report.json")
    atomic_json_write(output_path, output)
    print(f"\n验证结果已保存: {output_path}")
    
    return 0 if all_valid else 1


if __name__ == "__main__":
    sys.exit(main())
