# Simple Multi-Camera Project

   Beginner-friendly multi-camera project based on your Nexa code.

   ## Included pipelines

   - `dummy`
   - `ppe`
   - `people_counter`

   ## Quick flow

   `MongoDB -> main.py -> reader loop -> queue(10) -> pipeline -> aggregator -> alert -> MongoDB`

   ## Architecture diagram

   ```text
   Multi-Camera Processing Architecture

               +-------------------------------+
               |         MongoDB cameras       |
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

   ## Important docs

   - `docs/PROJECT_MANUAL.md`
   - `docs/PROJECT_DETAILS.md`
   - `docs/ARCHITECTURE_DIAGRAM.txt`

   ## Quick start

   ```bash
   pip install -r requirements.txt
   python main.py --tenant_id <tenant_id> --camera_ids <cam1> <cam2> ...
   ```

   ## Notes

   - PPE model is included in `pipelines/ppe/models/best.pt`
   - People counter code is included. Default model path is `yolov8n.pt`
   - Configure MongoDB settings in a `.env` file
   - Use `LOG_LEVEL` in `.env` to set `DEBUG`, `INFO`, `WARNING`, or `ERROR`
   - Change camera settings in MongoDB `nexa_simple.cameras` collection
