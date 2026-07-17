"""
素材质量审计脚本 - Phase 1
默认对素材清单中的全部素材进行：
1. 文字审计（OCR 采样）
2. 产品一致性审计
3. 输出审计报告
"""
import os
import sys
import json
import csv
import subprocess
import logging
import argparse
import tempfile
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR


def atomic_json_write(filepath: str, data: Any) -> None:
    """原子写入 JSON 文件，确保 UTF-8 编码和写入后验证"""
    directory = os.path.dirname(filepath) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".json-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp_path, "r", encoding="utf-8") as f:
            json.load(f)
        os.replace(tmp_path, filepath)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

WORKSPACE = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
RUNS_DIR = os.path.join(WORKSPACE, "runs")
ASSETS_DIR = os.path.join(WORKSPACE, "assets")
AUDIT_DIR = os.path.join(WORKSPACE, "素材质量优化")

WHITELIST_ASSET_ID = "屏显调温_003"
WHITELIST_CANONICAL_NAME = "屏显调温_003_温度模式_3s"
TARGET_BRAND = "Creade"
TARGET_PRODUCT = "折叠高速吹风机"

ALLOWED_TEXT_PATTERNS = [
    "Creade", "creade", "CREADE", "科瑞德",
    "高速折叠吹风机", "折叠吹风机", "吹风机",  # Product description on packaging
]

FORBIDDEN_TEXT_PATTERNS = [
    "限时", "秒杀", "优惠", "折扣", "促销", "特价", "抢购", "爆款", "热卖",
    "新品", "上市", "福利", "到手价", "直降", "券后",
    "字幕", "标题", "关注", "点赞", "收藏", "转发",
    "REC", "4K", "HD", "UHD", "LOG",
    "戴森", "Dyson", "飞利浦", "Philips", "松下", "Panasonic",
]

PRODUCT_RELATED_TAGS = [
    "产品展示", "手持展示", "手持大小对比", "折叠动作",
    "屏显调温", "风力展示", "吹发动作", "护发效果",
    "风嘴配件", "放进包包", "放进行李箱", "赠品展示",
    "包装展示", "CTA促单", "价格促销",
]

PURE_SCENE_TAGS = ["旅行场景", "痛点共鸣"]


def resolve_manifest_path(explicit_path: Optional[str] = None) -> str:
    candidates = [
        explicit_path,
        os.getenv("ASSET_MANIFEST_PATH"),
        os.path.join(ASSETS_DIR, "asset_manifest_v2_bound.csv"),
        os.path.join(ASSETS_DIR, "asset_manifest_v2_bound.csv"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    raise FileNotFoundError(
        "未找到素材清单。请通过 --manifest 或 ASSET_MANIFEST_PATH 指定运行时素材 CSV"
    )


def _manifest_assets(manifest_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    manifest_path = resolve_manifest_path(manifest_path)
    assets: Dict[str, Dict[str, Any]] = {}
    with open(manifest_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            asset_id = (row.get("asset_id") or "").strip()
            if not asset_id:
                continue
            try:
                duration_sec = float(row.get("duration_sec") or 0)
            except (TypeError, ValueError):
                duration_sec = 0.0
            assets[asset_id] = {
                "asset_id": asset_id,
                "primary_scene_tag": row.get("primary_scene_tag", ""),
                "file_name": row.get("file_name", ""),
                "duration_sec": duration_sec,
                "description": row.get("description", ""),
                "s3_url": row.get("s3_url", ""),
                "url": row.get("s3_url", ""),
                "used_in_runs": [],
                "used_as_tags": set(),
                "clip_count": 0,
            }
    return assets


def collect_unique_assets(
    scope: str = "all",
    manifest_path: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    manifest_assets = _manifest_assets(manifest_path)
    assets: Dict[str, Dict[str, Any]] = (
        manifest_assets if scope == "all" else {}
    )
    for i in ["01", "02", "03", "04", "05"]:
        run_dir = os.path.join(RUNS_DIR, f"visual_opt_fix2_{i}")
        cr_path = os.path.join(run_dir, "clip_records.json")
        if not os.path.exists(cr_path):
            continue
        with open(cr_path) as f:
            clips = json.load(f)
        for clip in clips:
            if not clip.get("is_active_clip", clip.get("visual_group_is_main", True)):
                continue
            mid = clip.get("material_id", "")
            if not mid:
                continue
            if mid not in assets:
                manifest_item = manifest_assets.get(mid, {})
                assets[mid] = dict(manifest_item) if manifest_item else {
                    "asset_id": mid,
                    "url": clip.get("url", ""),
                    "used_in_runs": [],
                    "used_as_tags": set(),
                    "clip_count": 0,
                }
            assets[mid]["url"] = assets[mid].get("url") or clip.get("url", "")
            assets[mid]["used_in_runs"].append(f"visual_opt_fix2_{i}")
            assets[mid]["clip_count"] += 1

    for i in ["01", "02", "03", "04", "05"]:
        run_dir = os.path.join(RUNS_DIR, f"visual_opt_fix2_{i}")
        sa_path = os.path.join(run_dir, "selected_assets.json")
        if not os.path.exists(sa_path):
            continue
        with open(sa_path) as f:
            selected = json.load(f)
        for sa in selected:
            mid = sa.get("material_id", sa.get("asset_id", ""))
            tag = sa.get("matched_tag", sa.get("scene_tag", ""))
            if mid in assets and tag:
                assets[mid]["used_as_tags"].add(tag)

    for aid in assets:
        assets[aid]["used_as_tags"] = sorted(assets[aid]["used_as_tags"])
        assets[aid]["used_in_runs"] = sorted(set(assets[aid]["used_in_runs"]))
    return assets


def download_video(url: str, output_path: str) -> bool:
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return True
    try:
        cmd = ["curl", "-sL", "-o", output_path, "--max-time", "30", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"Download failed: {url} -> {e}")
        return False


def get_video_duration(video_path: str) -> float:
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        return frame_count / fps if fps > 0 else 0.0
    except Exception:
        return 0.0


def sample_frames(video_path: str, num_samples: int = 5) -> List[Tuple[float, np.ndarray]]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0
    ratios = [0.1, 0.5, 0.9] if duration < 2 else [0.1, 0.25, 0.5, 0.75, 0.9]
    frames = []
    for ratio in ratios[:num_samples]:
        frame_pos = int(total_frames * ratio)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
        ret, frame = cap.read()
        if ret:
            timestamp = frame_pos / fps if fps > 0 else 0
            frames.append((round(timestamp, 2), frame))
    cap.release()
    return frames


def run_ocr(ocr: RapidOCR, frame: np.ndarray) -> List[Dict[str, Any]]:
    try:
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result, _ = ocr(frame_rgb)
        detections = []
        if result:
            for item in result:
                bbox = item[0]
                text = item[1]
                confidence = item[2]
                h, w = frame.shape[:2]
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                box_w = max(xs) - min(xs)
                box_h = max(ys) - min(ys)
                box_area = box_w * box_h
                frame_area = w * h
                area_ratio = box_area / frame_area if frame_area > 0 else 0
                cy = (min(ys) + max(ys)) / 2 / h
                if cy < 0.15:
                    position = "top"
                elif cy < 0.4:
                    position = "upper"
                elif cy < 0.6:
                    position = "center"
                elif cy < 0.8:
                    position = "lower"
                else:
                    position = "bottom"
                detections.append({
                    "text": text,
                    "confidence": round(float(confidence), 3),
                    "bounding_box": [[int(p[0]), int(p[1])] for p in bbox],
                    "area_ratio": round(area_ratio, 4),
                    "position": position,
                })
        return detections
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return []


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def is_creade_misread(text: str) -> bool:
    """Check if text is an OCR misread of 'Creade' brand logo."""
    text_lower = text.lower().strip()
    # Direct match
    if text_lower in ["creade", "科瑞德"]:
        return True
    # Fuzzy match - Levenshtein distance <= 3 for strings of similar length
    if 4 <= len(text_lower) <= 10:
        dist = levenshtein_distance(text_lower, "creade")
        if dist <= 3:
            return True
    return False


def is_noise_or_artifact(text: str) -> bool:
    """Check if text is likely OCR noise or video compression artifact."""
    text = text.strip()
    # Single character - likely noise
    if len(text) <= 1:
        return True
    # Short number patterns (2-4 digits) - likely compression artifacts or timestamps
    if text.isdigit() and len(text) <= 4:
        return True
    # Pure numbers with length > 4 - likely compression artifacts
    if text.isdigit() and len(text) > 4:
        return True
    # Repeated digit patterns like "15555355555555555" - compression artifact
    if len(text) > 3 and sum(1 for c in text if c.isdigit()) / len(text) > 0.8:
        return True
    return False


def is_video_watermark(text: str) -> bool:
    """Check if text is a video editing software watermark."""
    text_upper = text.upper().strip()
    watermark_patterns = [
        "剪映", "CAPCUT", "INSHOT", "VIVAVIDEO", "快影", "必剪",
        "LAND", "DOL", "DO!", "ILAND", "DOLBY", "HD", "REC",
    ]
    return any(pattern in text_upper for pattern in watermark_patterns)


def classify_text(detections: List[Dict[str, Any]], asset_id: str, scene_tag: str) -> Dict[str, Any]:
    all_texts = [d["text"] for d in detections]
    combined_text = " ".join(all_texts)
    allowed_texts = []
    forbidden_texts = []
    
    # Filter out noise/artifacts first
    meaningful_texts = []
    noise_texts = []
    for t in all_texts:
        if is_noise_or_artifact(t):
            noise_texts.append(t)
        else:
            meaningful_texts.append(t)
    
    # Check for Creade brand logo misreads and allowed patterns
    creade_detected = False
    for t in meaningful_texts:
        if is_creade_misread(t):
            creade_detected = True
            if "Creade" not in allowed_texts:
                allowed_texts.append("Creade")
        # Check allowed patterns (brand name, product description on packaging)
        for pattern in ALLOWED_TEXT_PATTERNS:
            if pattern.lower() in t.lower() and pattern not in allowed_texts:
                allowed_texts.append(pattern)
                if pattern in ["科瑞德", "Creade", "creade", "CREADE"]:
                    creade_detected = True
    
    # Check for forbidden patterns in meaningful texts
    for pattern in FORBIDDEN_TEXT_PATTERNS:
        for t in meaningful_texts:
            if pattern.lower() in t.lower():
                if pattern not in forbidden_texts:
                    forbidden_texts.append(pattern)
    
    # Check for watermarks
    watermark_detected = any(is_video_watermark(t) for t in meaningful_texts)
    
    # Determine text type
    # Watermarks are NOT noise - they are forbidden text
    if not meaningful_texts or (len(meaningful_texts) <= 2 and all(is_noise_or_artifact(t) for t in meaningful_texts)):
        text_type = "none"  # Only noise, treat as no meaningful text
    elif watermark_detected and not any(is_brand_logo(t) or is_device_screen(t) for t in meaningful_texts):
        # Watermarks without allowed text -> video_watermark (rejected)
        text_type = "video_watermark"
    elif forbidden_texts:
        marketing_words = ["限时", "秒杀", "优惠", "折扣", "促销", "特价", "抢购", "爆款", "热卖", "新品", "上市", "福利", "到手价", "直降", "券后"]
        other_brand = ["戴森", "Dyson", "飞利浦", "Philips", "松下", "Panasonic"]
        platform_words = ["REC", "4K", "HD", "UHD", "LOG"]
        subtitle_words = ["字幕", "标题", "关注", "点赞", "收藏", "转发"]
        for ft in forbidden_texts:
            if ft in marketing_words:
                text_type = "marketing_text"
                break
            elif ft in other_brand:
                text_type = "other_brand"
                break
            elif ft in platform_words:
                text_type = "platform_decoration"
                break
            elif ft in subtitle_words:
                text_type = "subtitle_overlay"
                break
        else:
            text_type = "template_text"
    elif creade_detected:
        text_type = "brand_logo"
    elif scene_tag == "屏显调温":
        text_type = "device_screen"
    elif watermark_detected:
        text_type = "video_watermark"
    else:
        bottom_texts = [d for d in detections if d["position"] in ["bottom", "lower"] and not is_noise_or_artifact(d["text"])]
        if bottom_texts and any(d["area_ratio"] > 0.05 for d in bottom_texts):
            text_type = "burned_subtitle"
        else:
            text_type = "unknown_text"
    return {
        "all_detected_texts": all_texts,
        "meaningful_texts": meaningful_texts,
        "noise_texts": noise_texts,
        "allowed_texts": allowed_texts,
        "forbidden_texts": forbidden_texts,
        "text_type": text_type,
        "combined_text": combined_text,
        "creade_detected": creade_detected,
        "watermark_detected": watermark_detected,
    }


def determine_text_audit_status(text_type: str, asset_id: str, scene_tag: str, forbidden_texts: List[str], watermark_detected: bool = False) -> Tuple[str, str]:
    if asset_id == WHITELIST_ASSET_ID:
        return "passed_with_exception", "唯一白名单素材，用户确认原生字幕内容正确"
    if text_type == "none":
        return "passed", "未检测到有意义的文字（仅噪声或压缩伪影）"
    if text_type == "brand_logo":
        return "passed", "仅检测到品牌Logo/Creade文字，属于允许文字"
    if text_type == "device_screen" and scene_tag == "屏显调温":
        return "passed", "屏显素材上的设备屏幕数字显示，属于设备真实显示内容"
    if text_type == "video_watermark":
        # Video editing watermarks (LAND, DOL, etc.) indicate non-professional source
        return "rejected", f"检测到视频编辑软件水印: {forbidden_texts}"
    if text_type == "other_brand":
        return "rejected", f"检测到其他品牌文字: {forbidden_texts}"
    if text_type == "marketing_text":
        return "rejected", f"检测到营销文字: {forbidden_texts}"
    if text_type == "burned_subtitle":
        return "rejected", "检测到烧录字幕，与系统字幕区域冲突"
    if text_type == "platform_decoration":
        return "rejected", f"检测到平台装饰文字: {forbidden_texts}"
    if text_type == "template_text":
        return "rejected", f"检测到模板/标签文字: {forbidden_texts}"
    if text_type == "subtitle_overlay":
        return "rejected", f"检测到字幕叠加文字: {forbidden_texts}"
    if text_type == "unknown_text":
        return "rejected", "检测到未分类文字，无法确认安全性"
    return "unverified", f"未覆盖的文字类型: {text_type}"


def _load_confirmed_product_assets() -> Dict[str, Dict[str, Any]]:
    path = os.path.join(ASSETS_DIR, "product_4070_safe_whitelist.json")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        rows = json.load(handle)
    return {
        row["asset_id"]: row
        for row in rows
        if row.get("asset_id") and row.get("manually_confirmed") is True
    }


def check_product_consistency(asset_id: str, scene_tag: str, ocr_texts: List[str]) -> Tuple[str, str]:
    combined = " ".join(ocr_texts).lower()
    other_brands = ["戴森", "dyson", "飞利浦", "philips", "松下", "panasonic"]
    for brand in other_brands:
        if brand in combined:
            return "rejected", f"检测到其他品牌: {brand}"
    if scene_tag in PURE_SCENE_TAGS:
        return "passed", "纯场景素材，不要求展示产品"
    if scene_tag in PRODUCT_RELATED_TAGS:
        confirmation = _load_confirmed_product_assets().get(asset_id)
        if confirmation:
            allowed_tags = confirmation.get("allowed_scene_tags") or []
            if not allowed_tags or scene_tag in allowed_tags:
                return "passed", "已通过人工产品白名单确认目标产品一致性"
        return "unverified", "产品相关素材，需人工确认目标产品一致性"
    return "unverified", "非产品相关标签，产品一致性不适用"


def audit_asset(ocr: RapidOCR, asset_id: str, asset_info: Dict[str, Any], evidence_dir: str) -> Dict[str, Any]:
    url = asset_info.get("s3_url") or asset_info.get("url", "")
    scene_tag = asset_info.get("primary_scene_tag", "")
    file_name = asset_info.get("file_name", "")
    result = {
        "asset_id": asset_id, "file_name": file_name, "primary_scene_tag": scene_tag,
        "url": url, "source_path": url, "duration_sec": asset_info.get("duration_sec", 0),
        "description": asset_info.get("description", ""),
        "used_in_runs": asset_info.get("used_in_runs", []),
        "used_as_tags": asset_info.get("used_as_tags", []),
        "ocr_engine": "rapidocr_onnxruntime", "ocr_available": True,
        "sampled_frames": [], "detected_texts": [], "allowed_texts": [],
        "forbidden_texts": [], "text_type": "none", "text_risk_level": "none",
        "text_audit_status": "unverified", "text_audit_reason": "",
        "product_consistency_status": "unverified", "product_consistency_reason": "",
        "subtitle_region_conflict": False, "sampled_frame_count": 0,
        "evidence_frames": [], "is_whitelisted": asset_id == WHITELIST_ASSET_ID,
    }
    video_dir = os.path.join(evidence_dir, "videos")
    os.makedirs(video_dir, exist_ok=True)
    video_path = os.path.join(video_dir, f"{asset_id}.mp4")
    if not download_video(url, video_path):
        result["text_audit_status"] = "unverified"
        result["text_audit_reason"] = "视频下载失败"
        result["product_consistency_status"] = "unverified"
        result["product_consistency_reason"] = "视频下载失败"
        return result
    duration = get_video_duration(video_path)
    if duration > 0:
        result["duration_sec"] = round(duration, 2)
    frames = sample_frames(video_path)
    if not frames:
        result["text_audit_status"] = "unverified"
        result["text_audit_reason"] = "无法采样帧"
        return result
    asset_evidence_dir = os.path.join(evidence_dir, asset_id)
    os.makedirs(asset_evidence_dir, exist_ok=True)
    all_detections = []
    for timestamp, frame in frames:
        frame_path = os.path.join(asset_evidence_dir, f"frame_{timestamp}s.jpg")
        cv2.imwrite(frame_path, frame)
        detections = run_ocr(ocr, frame)
        for d in detections:
            d["sampled_time"] = timestamp
            d["evidence_frame"] = frame_path
        all_detections.extend(detections)
        result["sampled_frames"].append({
            "sampled_time": timestamp, "frame_path": frame_path,
            "detected_text_count": len(detections),
        })
    result["sampled_frame_count"] = len(frames)
    result["detected_texts"] = all_detections
    result["evidence_frames"] = [sf["frame_path"] for sf in result["sampled_frames"]]
    text_class = classify_text(all_detections, asset_id, scene_tag)
    result["allowed_texts"] = text_class["allowed_texts"]
    result["forbidden_texts"] = text_class["forbidden_texts"]
    result["text_type"] = text_class["text_type"]
    status, reason = determine_text_audit_status(text_class["text_type"], asset_id, scene_tag, text_class["forbidden_texts"], text_class.get("watermark_detected", False))
    result["text_audit_status"] = status
    result["text_audit_reason"] = reason
    bottom_detections = [d for d in all_detections if d["position"] in ["bottom", "lower"]]
    if bottom_detections and any(d["area_ratio"] > 0.05 for d in bottom_detections):
        result["subtitle_region_conflict"] = True
    if status == "rejected":
        result["text_risk_level"] = "high"
    elif status == "passed_with_exception":
        result["text_risk_level"] = "medium"
    elif status == "passed":
        result["text_risk_level"] = "low"
    else:
        result["text_risk_level"] = "unknown"
    all_text_strs = [d["text"] for d in all_detections]
    prod_status, prod_reason = check_product_consistency(asset_id, scene_tag, all_text_strs)
    result["product_consistency_status"] = prod_status
    result["product_consistency_reason"] = prod_reason
    return result


def main():
    parser = argparse.ArgumentParser(description="素材质量审计")
    parser.add_argument(
        "--scope",
        choices=("all", "used"),
        default="all",
        help="all=审计完整素材清单；used=仅审计五次历史运行实际使用素材",
    )
    parser.add_argument(
        "--manifest",
        help="素材清单 CSV 路径；也可通过 ASSET_MANIFEST_PATH 指定",
    )
    args = parser.parse_args()
    logger.info("=== 素材质量审计 Phase 1 ===")
    logger.info("Initializing RapidOCR...")
    ocr = RapidOCR()
    logger.info("RapidOCR ready")
    logger.info("Collecting assets (scope=%s)...", args.scope)
    manifest_path = resolve_manifest_path(args.manifest)
    assets = collect_unique_assets(args.scope, manifest_path)
    logger.info("Found %d assets", len(assets))
    evidence_dir = os.path.join(AUDIT_DIR, "material_audit_evidence")
    os.makedirs(evidence_dir, exist_ok=True)
    audit_results = []
    for asset_id, asset_info in sorted(assets.items()):
        logger.info(f"Auditing: {asset_id} ({asset_info.get('primary_scene_tag', '')})")
        result = audit_asset(ocr, asset_id, asset_info, evidence_dir)
        audit_results.append(result)
        logger.info(f"  -> text_audit={result['text_audit_status']}, product={result['product_consistency_status']}")
    
    # Generate summary
    total = len(audit_results)
    passed = sum(1 for r in audit_results if r["text_audit_status"] == "passed")
    pwe = sum(1 for r in audit_results if r["text_audit_status"] == "passed_with_exception")
    rejected = sum(1 for r in audit_results if r["text_audit_status"] == "rejected")
    unverified = sum(1 for r in audit_results if r["text_audit_status"] == "unverified")
    prod_passed = sum(1 for r in audit_results if r["product_consistency_status"] == "passed")
    prod_rejected = sum(1 for r in audit_results if r["product_consistency_status"] == "rejected")
    
    scene_tags = {}
    for r in audit_results:
        tag = r.get("primary_scene_tag", "")
        if tag:
            if tag not in scene_tags:
                scene_tags[tag] = {"total": 0, "safe": 0, "rejected": 0}
            scene_tags[tag]["total"] += 1
            if r["text_audit_status"] in ["passed", "passed_with_exception"]:
                scene_tags[tag]["safe"] += 1
            elif r["text_audit_status"] == "rejected":
                scene_tags[tag]["rejected"] += 1
    
    safe_assets = sorted([r["asset_id"] for r in audit_results if r["text_audit_status"] in ["passed", "passed_with_exception"] and r["product_consistency_status"] == "passed"])
    rejected_assets = sorted([r["asset_id"] for r in audit_results if r["text_audit_status"] == "rejected" or r["product_consistency_status"] == "rejected"])
    
    summary = {
        "audit_version": "1.1", "audit_time": datetime.now().isoformat(),
        "audit_scope": args.scope,
        "manifest_path": manifest_path,
        "manifest_asset_count": len(_manifest_assets(manifest_path)),
        "total_assets_audited": total,
        "text_audit_summary": {"passed": passed, "passed_with_exception": pwe, "rejected": rejected, "unverified": unverified},
        "product_consistency_summary": {"passed": prod_passed, "rejected": prod_rejected, "unverified": total - prod_passed - prod_rejected},
        "scene_tag_coverage": scene_tags,
        "safe_assets": safe_assets, "rejected_assets": rejected_assets,
        "whitelist_assets": [WHITELIST_ASSET_ID], "whitelist_count": 1,
    }
    
    os.makedirs(AUDIT_DIR, exist_ok=True)
    atomic_json_write(os.path.join(AUDIT_DIR, "material_audit_detail.json"), audit_results)
    atomic_json_write(os.path.join(AUDIT_DIR, "material_audit_summary.json"), summary)
    
    # Whitelist
    whitelist = []
    for r in audit_results:
        if r["asset_id"] == WHITELIST_ASSET_ID:
            whitelist.append({
                "asset_id": r["asset_id"], "canonical_name": WHITELIST_CANONICAL_NAME,
                "native_text_type": "screen_mode_split", "has_burned_in_text": True,
                "native_text_allowed": True, "text_audit_status": "passed_with_exception",
                "suppress_generated_subtitle": True, "manually_confirmed": True,
                "allowed_scene_tags": ["屏显调温", "风温切换", "温度模式", "档位展示"],
                "whitelist_reason": "用户确认该分屏素材原生字幕内容正确",
                "evidence_frames": r["evidence_frames"], "url": r["url"],
                "source_path": r["source_path"], "duration_sec": r["duration_sec"],
            })
    atomic_json_write(os.path.join(AUDIT_DIR, "native_text_whitelist.json"), whitelist)
    
    # Rejected
    rejected_list = [{"asset_id": r["asset_id"], "text_audit_status": r["text_audit_status"],
        "text_audit_reason": r["text_audit_reason"], "product_consistency_status": r["product_consistency_status"],
        "product_consistency_reason": r["product_consistency_reason"],
        "forbidden_texts": r["forbidden_texts"], "evidence_frames": r["evidence_frames"]}
        for r in audit_results if r["text_audit_status"] == "rejected" or r["product_consistency_status"] == "rejected"]
    atomic_json_write(os.path.join(AUDIT_DIR, "rejected_assets.json"), rejected_list)
    
    # Safe
    safe_list = [{"asset_id": r["asset_id"], "primary_scene_tag": r["primary_scene_tag"],
        "text_audit_status": r["text_audit_status"], "product_consistency_status": r["product_consistency_status"],
        "safe_for_scene_tags": [r["primary_scene_tag"]], "forbidden_for_scene_tags": [],
        "manually_whitelisted": r["asset_id"] == WHITELIST_ASSET_ID,
        "suppress_generated_subtitle": r["asset_id"] == WHITELIST_ASSET_ID,
        "audit_evidence_paths": r["evidence_frames"]}
        for r in audit_results if r["text_audit_status"] in ["passed", "passed_with_exception"] and r["product_consistency_status"] == "passed"]
    atomic_json_write(os.path.join(AUDIT_DIR, "safe_assets.json"), safe_list)
    
    logger.info("=" * 60)
    logger.info(f"Audit Summary: total={total}, passed={passed}, pwe={pwe}, rejected={rejected}, unverified={unverified}")
    logger.info(f"Product: passed={prod_passed}, rejected={prod_rejected}")
    logger.info(f"Safe: {len(safe_assets)}, Rejected: {len(rejected_assets)}, Whitelist: 1")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
