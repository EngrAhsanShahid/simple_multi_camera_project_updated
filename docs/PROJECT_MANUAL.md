# Project Manual

   This project is a simplified version of your Nexa flow. It is designed to be easy to read and easy to modify.

   ## Main idea

   Each camera runs in two loops:

   1. **Reader loop**: read frames and push the latest frames into a queue.
   2. **Processor loop**: read frames from the queue, run pipelines, aggregate results, and save alerts.

   ## Architecture diagram

   ```text
   Multi-Camera Processing Architecture

               +-------------------------------+
               |       MongoDB cameras         |
               |  (all camera configurations) |
               +---------------+---------------+
                               |
                               v
               +-------------------------------+
               |            main.py            |
               |       starts all tasks        |
               +---------------+---------------+
                               |
       +-----------------------+------------------------+
       |                       |                        |
       v                       v                        v
+--------------+       +--------------+        +--------------+
| Camera 1     |       | Camera 2     |  ...   | Camera N     |
+------+-------+       +------+-------+        +------+-------+
       |                      |                         |
       v                      v                         v
+--------------------------------------------------------------+
|               camera_reader_task   (LOOP 1)                  |
|  SourceReader -> FrameSampler -> FramePacket -> put_latest   |
+------------------------------+-------------------------------+
                               |
                               v
                    +-------------------------+
                    |  Async Queue (size 10)  |
                    | latest frames only kept |
                    +------------+------------+
                                 |
                                 v
+--------------------------------------------------------------+
|                 processor_task   (LOOP 2)                    |
| queue.get() -> PipelineManager -> Aggregator -> AlertEngine |
+------------------------------+-------------------------------+
                               |
                               v
                    +-------------------------+
                    |      mongo_store.py     |
                    | save alert with alert_id|
                    +-------------------------+
   ```

   ## Pipelines included

   - `dummy`: test pipeline, works without any AI model.
   - `ppe`: real PPE pipeline using the model from the original Nexa code (`pipelines/ppe/models/best.pt`).
   - `people_counter`: people counting pipeline. By default it uses `yolov8n.pt`. If you are offline, place your own YOLO weights file and update `pipelines/people_counter/config.json`.

   ## How to run

   ```bash
   pip install -r requirements.txt
   python main.py --tenant_id <tenant_id> --camera_ids <cam1> <cam2> ...
   ```

   ## What to change first

   1. Update camera records in MongoDB `naxa_simple.cameras`
   2. Change `source_path`
   3. Choose pipelines for each camera
   4. Start MongoDB
   5. Run the project

   ## Queue behavior

   Queue size is `10`.

   If the queue is full:
   - the oldest frame is dropped
   - the newest frame is added

   This keeps the system near real time.

   ## Alert behavior

   Alerts are created for labels like:
   - `helmet_missing`
   - `mask_missing`
   - `vest_missing`
   - anything containing `missing`, `no_`, or `violation`

   ## Model notes

   ### PPE
   - Already included in this zip as `pipelines/ppe/models/best.pt`
   - Config file: `pipelines/ppe/config.json`

   ### People counter
   - Code is included.
   - Config file: `pipelines/people_counter/config.json`
   - Default model path is `yolov8n.pt`
   - If the file does not exist, Ultralytics may try to download it on first run.
   - If you are offline, copy your YOLO weights manually and update the config.

   ## File map

   - `main.py`: starts the project and launches all loops
   - `contracts.py`: all main Pydantic models
   - `source_reader.py`: reads RTSP or file frames
   - `frame_sampler.py`: skips frames to match target FPS
   - `pipeline_manager.py`: loads and runs requested pipelines
   - `aggregator.py`: combines pipeline outputs for the same frame
   - `alert_engine.py`: converts detections into alerts
   - `mongo_store.py`: inserts alerts into MongoDB
   - `pipelines/`: pipeline code and model files
