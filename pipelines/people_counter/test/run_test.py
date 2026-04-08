# pipelines/people_counter/test/run_test.py
"""
Standalone People Counter Pipeline Test Script.

Processes all videos in input_videos/, runs YOLO person detection with
ByteTrack tracking on each frame, draws annotated bounding boxes with
track IDs and live counts, saves output videos to output_videos/, and
logs run metadata to test_results.csv.

Usage:
    cd pipelines/people_counter/test
    python run_test.py

    Or from project root:
    python pipelines/people_counter/test/run_test.py
"""

import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

# --- Paths ---
SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = PIPELINE_DIR.parent.parent

INPUT_DIR = SCRIPT_DIR / "input_videos"
OUTPUT_DIR = SCRIPT_DIR / "output_videos"
CSV_PATH = SCRIPT_DIR / "test_results.csv"
CONFIG_PATH = PIPELINE_DIR / "config.json"

# Video file extensions to process
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".wmv"}

# Annotation colors (BGR)
COLOR_BBOX = (255, 180, 0)      # Cyan-ish for person boxes
COLOR_ID_TEXT = (255, 255, 255)  # White text
COLOR_OVERLAY_BG = (40, 40, 40) # Dark overlay background

CSV_COLUMNS = [
    "run_timestamp",
    "input_video",
    "video_duration_sec",
    "video_fps",
    "video_resolution",
    "total_frames",
    "total_detections",
    "total_unique_people",
    "max_visible_at_once",
    "avg_visible_per_frame",
    "avg_confidence",
    "processing_time_sec",
    "avg_inference_ms",
    "model_path",
    "confidence_threshold",
    "device",
    "output_video",
]


def load_config() -> dict:
    """Load pipeline config from config.json."""
    with open(CONFIG_PATH) as f:
        return json.load(f)


def draw_detections(
    frame: np.ndarray,
    results,
    model: YOLO,
    seen_ids: set[int],
) -> tuple[np.ndarray, list, set[int]]:
    """Draw bounding boxes with track IDs and count overlay on a frame.

    Args:
        frame: BGR image (numpy array).
        results: YOLO tracking results for this frame.
        model: YOLO model instance.
        seen_ids: Set of all unique track IDs seen so far.

    Returns:
        Tuple of (annotated frame, list of detection dicts, updated seen_ids).
    """
    annotated = frame.copy()
    detections = []
    frame_ids: list[int] = []

    boxes = results[0].boxes
    if boxes is not None and len(boxes) > 0:
        for box in boxes:
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            confidence = float(box.conf[0].item())

            # Get track ID
            track_id = int(box.id[0].item()) if box.id is not None else -1
            if track_id >= 0:
                frame_ids.append(track_id)
                seen_ids.add(track_id)

            # Draw bounding box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), COLOR_BBOX, 2)

            # Draw track ID label
            if track_id >= 0:
                label = f"Person #{track_id} ({confidence:.2f})"
            else:
                label = f"Person ({confidence:.2f})"

            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(annotated, (x1, y1 - th - 8), (x1 + tw + 4, y1), COLOR_BBOX, -1)
            cv2.putText(
                annotated, label, (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
            )

            detections.append({
                "track_id": track_id,
                "confidence": confidence,
            })

    # Draw count overlay (top-left)
    visible = len(frame_ids) if frame_ids else len(detections)
    total = len(seen_ids)
    overlay_text = f"Visible: {visible} | Total Unique: {total}"

    (tw, th), _ = cv2.getTextSize(overlay_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    cv2.rectangle(annotated, (8, 8), (tw + 20, th + 20), COLOR_OVERLAY_BG, -1)
    cv2.putText(
        annotated, overlay_text, (14, th + 14),
        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA,
    )

    return annotated, detections, seen_ids


def process_video(video_path: Path, config: dict, model: YOLO) -> dict:
    """Process a single video file through the people counter pipeline.

    Args:
        video_path: Path to input video.
        config: Pipeline config dict.
        model: Loaded YOLO model.

    Returns:
        Dictionary with run metadata for CSV logging.
    """
    confidence_threshold = config.get("confidence_threshold", 0.5)
    device = config.get("device", "cpu")
    person_class_id = config.get("person_class_id", 0)

    # Open video
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  ERROR: Cannot open {video_path.name}")
        return {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_video_frames / fps if fps > 0 else 0

    # Output path
    output_name = f"{video_path.stem}_output.mp4"
    output_path = OUTPUT_DIR / output_name

    # Video writer
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    print(f"  Input:  {video_path.name} ({width}x{height} @ {fps:.1f}fps, {duration_sec:.1f}s)")
    print(f"  Output: {output_name}")

    # Processing state
    frame_count = 0
    total_detections = 0
    seen_ids: set[int] = set()
    all_confidences: list[float] = []
    inference_times: list[float] = []
    visible_counts: list[int] = []
    max_visible = 0

    start_time = time.monotonic()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1

        # Run tracking
        t0 = time.monotonic()
        results = model.track(
            frame,
            persist=True,
            conf=confidence_threshold,
            classes=[person_class_id],
            device=device,
            verbose=False,
        )
        inference_ms = (time.monotonic() - t0) * 1000.0
        inference_times.append(inference_ms)

        # Draw annotations
        annotated, detections, seen_ids = draw_detections(frame, results, model, seen_ids)
        writer.write(annotated)

        # Accumulate stats
        visible = len(detections)
        total_detections += visible
        visible_counts.append(visible)
        max_visible = max(max_visible, visible)

        for det in detections:
            all_confidences.append(det["confidence"])

        # Progress update every 100 frames
        if frame_count % 100 == 0:
            elapsed = time.monotonic() - start_time
            fps_actual = frame_count / elapsed if elapsed > 0 else 0
            print(f"    Frame {frame_count}/{total_video_frames} "
                  f"({fps_actual:.1f} fps, visible={visible}, unique={len(seen_ids)})")

    processing_time = time.monotonic() - start_time

    cap.release()
    writer.release()

    avg_confidence = round(sum(all_confidences) / len(all_confidences), 4) if all_confidences else 0.0
    avg_inference = round(sum(inference_times) / len(inference_times), 2) if inference_times else 0.0
    avg_visible = round(sum(visible_counts) / len(visible_counts), 2) if visible_counts else 0.0

    print(f"  Done: {frame_count} frames, {len(seen_ids)} unique people, "
          f"max {max_visible} visible at once, {processing_time:.1f}s")

    return {
        "run_timestamp": datetime.now().isoformat(timespec="seconds"),
        "input_video": video_path.name,
        "video_duration_sec": round(duration_sec, 2),
        "video_fps": round(fps, 2),
        "video_resolution": f"{width}x{height}",
        "total_frames": frame_count,
        "total_detections": total_detections,
        "total_unique_people": len(seen_ids),
        "max_visible_at_once": max_visible,
        "avg_visible_per_frame": avg_visible,
        "avg_confidence": avg_confidence,
        "processing_time_sec": round(processing_time, 2),
        "avg_inference_ms": avg_inference,
        "model_path": config.get("model_path", ""),
        "confidence_threshold": confidence_threshold,
        "device": device,
        "output_video": output_name,
    }


def append_to_csv(row: dict) -> None:
    """Append a result row to the CSV file, creating it if needed."""
    file_exists = CSV_PATH.exists()

    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    """Run the standalone people counter pipeline test."""
    print("=" * 60)
    print("People Counter Pipeline — Standalone Video Test")
    print("=" * 60)

    # Ensure directories exist
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load config
    if not CONFIG_PATH.exists():
        print(f"ERROR: Config not found at {CONFIG_PATH}")
        sys.exit(1)

    config = load_config()
    print(f"Config: threshold={config.get('confidence_threshold')}, "
          f"device={config.get('device')}, max_det={config.get('max_detections')}")

    # Resolve model path
    model_path = config.get("model_path", "")
    if not Path(model_path).is_absolute():
        model_path = str(PROJECT_ROOT / model_path)

    # Load model
    print(f"Loading model: {model_path}")
    model = YOLO(model_path)
    print(f"Model loaded — person class: '{model.names[config.get('person_class_id', 0)]}'")

    # Find input videos
    videos = sorted(
        p for p in INPUT_DIR.iterdir()
        if p.suffix.lower() in VIDEO_EXTENSIONS
    )

    if not videos:
        print(f"\nNo videos found in {INPUT_DIR}")
        print("Place .mp4/.avi/.mkv files in input_videos/ and run again.")
        sys.exit(0)

    print(f"\nFound {len(videos)} video(s) to process:\n")

    # Process each video
    for i, video_path in enumerate(videos, 1):
        print(f"[{i}/{len(videos)}] Processing: {video_path.name}")
        row = process_video(video_path, config, model)
        if row:
            append_to_csv(row)
        print()

    print("=" * 60)
    print(f"Results logged to: {CSV_PATH}")
    print(f"Output videos in:  {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
