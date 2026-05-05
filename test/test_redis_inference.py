import asyncio
import time
import cv2
import sys
import os
from pathlib import Path

# Add the project root to sys.path so imports work correctly
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from pipelines.ppe.pipeline import PPEPipeline, load_pipeline_config
from contracts.frame_packet import FramePacket
from contracts.pipeline_result import PipelineResult, Detection

async def _process_raw_to_result(pipeline, raw_results, packet: FramePacket, start_time: float) -> PipelineResult:
    detections: list[Detection] = []
    boxes = raw_results.get("boxes", [])
    classes = raw_results.get("classes", [])
    confidences = raw_results.get("confidence", [])
    labels = raw_results.get("labels", [])

    if boxes is not None and len(boxes) > 0:
        for box,class_id,confidence,label in zip(boxes, classes, confidences, labels):
            x1, y1, x2, y2 = box
            w = int(x2 - x1)
            h = int(y2 - y1)
            detections.append(Detection(
                label=label,
                bbox=[x1,y1,w,h],
                confidence=round(confidence,4),
                metadata={"class_id": class_id}
            ))

    elapsed_ms = (time.monotonic() - start_time) * 1000.0
    return PipelineResult(
        tenant_id=packet.tenant_id,
        camera_id=packet.camera_id,
        frame_id=packet.frame_id,
        timestamp=packet.timestamp,
        pipeline_id=pipeline.pipeline_id,
        detections=detections,
        processing_time_ms=elapsed_ms,
        is_complete=True
    )

async def main():
    print("--- PPE Redis Inference Profiler ---")
    print("Loading configuration...")
    
    # Load pipeline configuration and connect to Redis
    config = load_pipeline_config()
    
    # Optionally override the timeout to allow slow workers to finish during profiling
    config["result_timeout_sec"] = 15.0 
    config["redis_url"] = "redis://192.168.100.4:6379" 
    
    pipeline = PPEPipeline(config)
    await pipeline.setup()
    print(f"Connected to Redis (URL: {pipeline._redis_url})")
    print(f"Result timeout set to: {pipeline._result_timeout_sec} seconds")
    
    # Load a test frame
    video_path = Path("C:\\Users\\anas\\Documents\\LT\\NEXA\\SOPs-demo\\deliver_vids\\demo_testcases\\demo_test_video_01.mp4")
    # video_path = project_root / "ppe_test.mp4"
    if not video_path.exists():
        print(f"\nWARNING: Video file not found: {video_path}")
        # dummy fallback will be handled later
    
    print("\nStarting inference profiling loop...")
    
    num_iterations = 60
    latencies = []
    
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print("Error: Could not open video file.")
        import numpy as np
        blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    
    for i in range(num_iterations):
        if cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
        else:
            frame = blank_frame
        packet = FramePacket(
            tenant_id="profiling_tenant",
            camera_id="profiling_cam",
            frame_id=f"profiling_frame_{i}",
            timestamp=time.time(),
            frame=frame,
            width=frame.shape[1],
            height=frame.shape[0],
            pipelines=["ppe"]
        )
        
        print(f"\n[{i+1}/{num_iterations}] Sending frame to Redis...")
        start_time = time.monotonic()
        
        try:
            # 1. Directly profile JUST the "send to redis" step
            send_start = time.monotonic()
            await pipeline.producer.publish_frame(
                stream_name="ppe",
                request_id=packet.frame_id,
                frame=packet.frame,
                maxlen=20000,
            )
            raw_send_time = time.monotonic() - send_start
            print(f"  -> Successfully pushed to Redis in {raw_send_time:.4f}s")
            
            # 2. Directly profile JUST the "wait for redis response" step
            wait_start = time.monotonic()
            raw_results = await asyncio.wait_for(
                pipeline.producer.get_results(packet.frame_id),
                timeout=pipeline._result_timeout_sec,
            )
            raw_wait_time = time.monotonic() - wait_start
            
            print(f"  -> Received worker response from Redis in {raw_wait_time:.4f}s")
            print(f"  -> RAW WORKER RESPONSE DICT: {raw_results}")
            
            # Pack it through pipeline for the rest of loop to not break
            pipeline_result = await _process_raw_to_result(pipeline, raw_results, packet, start_time)
            
        except asyncio.TimeoutError:
            print("  -> ERROR: Worker NEVER REPLIED. Hit hard timeout limit!")
            pipeline_result = None
            
        latency = time.monotonic() - start_time
        latencies.append(latency)
        
        if pipeline_result and pipeline_result.detections:
            print(f"[{i+1}/{num_iterations}] SUCCESS! Detections ({len(pipeline_result.detections)}):")
            for det in pipeline_result.detections:
                print(f"           - {det.label} (confidence: {det.confidence:.2f})")
        else:
            print(f"[{i+1}/{num_iterations}] FAILED or TIMED OUT after {latency:.4f} seconds. No detections returned.")
            
    await pipeline.close()
    
    # Print profiling summary
    print("\n==================================")
    print("      PROFILING SUMMARY           ")
    print("==================================")
    print(f"Total Frames Sent : {num_iterations}")
    print(f"Average Latency   : {sum(latencies)/len(latencies):.4f} seconds")
    print(f"Min Latency       : {min(latencies):.4f} seconds")
    print(f"Max Latency       : {max(latencies):.4f} seconds")
    
    # Check if we are hitting the 500ms Aggregator limit
    if sum(latencies)/len(latencies) > 0.5:
        print("\nDIAGNOSIS: Average latency > 0.5s.")
        print("Your worker is too slow to meet the current AGGREGATION_TIMEOUT_MS (500ms)!")
        print("You must either optimize the worker, scale up workers, or increase AGGREGATION_TIMEOUT_MS in .env.")
    else:
        print("\nDIAGNOSIS: Worker speed is healthy and well within the 500ms aggregator timeout.")

if __name__ == "__main__":
    # Workaround for Windows environments if applicable
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProfiling interrupted.")