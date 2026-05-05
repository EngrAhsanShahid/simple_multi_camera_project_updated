# Project Manual

A practical, end-to-end guide for running and modifying the single-camera worker.

## Main idea

One worker process is bound to one camera. Two `asyncio` tasks run concurrently
inside an `asyncio.TaskGroup` (so a crash in one cancels the other):

1. **Reader loop** (`camera_reader_task`): reads frames from RTSP/file,
   keeps every raw frame in a bounded ring buffer (for clip generation),
   and pushes sampled frames into the reader→processor queue.
2. **Processor loop** (`processor_task`): pulls frames from the queue,
   runs all configured pipelines concurrently with a timeout, evaluates rules,
   saves alerts (snapshot + clip + Mongo doc), and optionally pushes the
   annotated frame to LiveKit.

## Architecture diagram

See `docs/architecture_diagram.md` (Mermaid) or `docs/ARCHITECTURE_DIAGRAM.txt` (ASCII).

## Active pipelines

- **`ppe`** — Personal Protective Equipment violation detection.
  Runs **remotely**: publishes frames to Redis Stream `ppe`, GPU cluster consumes,
  runs YOLO, publishes results back. PPE waits up to `result_timeout_sec` for results.

To add a pipeline, see *Adding a new pipeline* below.

## How to run

### Prerequisites

- Python (with conda env, e.g. `yolo`)
- MongoDB running and reachable
- MinIO running and reachable
- Redis cluster + GPU consumer running and reachable (for PPE)
- A camera document in MongoDB `cameras` collection

### Camera document shape (MongoDB)

```json
{
  "tenant_id": "tenant_01",
  "camera_id": "cam_file_02",
  "source_path": "rtsp://user:pass@192.168.1.10/stream1",
  "target_fps": 10,
  "pipelines": ["ppe"],
  "enabled": true,
  "record_full_video": false
}
```

`source_path` may also be a local file path — `CameraConfig` auto-derives
`source_type` (RTSP vs FILE) from the prefix.

### Install + run

```bash
pip install -r requirements.txt
python main.py --tenant_id <tenant_id> --camera_id <camera_id>
```

To enable LiveKit live streaming:

```bash
python main.py --tenant_id <tenant_id> --camera_id <camera_id> --enable_livekit
```

If `--enable_livekit` is not passed, no frames are pushed to `frame_buffer`
(no wasted work).

## What to change first

1. Insert the camera document into MongoDB `cameras` collection (matching tenant + camera_id).
2. Set `source_path`, `target_fps`, and the `pipelines` list on the document.
3. Place per-camera rule overrides in `config/rules/{camera_id}.json` if you want
   anything other than `default.json`.
4. Configure `.env` with Mongo / MinIO / Redis / LiveKit URLs.
5. Start MongoDB, MinIO, Redis cluster + GPU consumer.
6. Run the worker.

## Queue behavior

The reader→processor queue is a bounded `asyncio.Queue` with `maxsize=QUEUE_MAXSIZE`
(env-driven). When full:

- The **oldest queued FramePacket is dropped** by `put_latest`.
- The **newest frame is added**.
- A `frame_dropped_queue_full` warning is logged so you can see when the
  worker is falling behind real time.

This is intentional backpressure — for live monitoring, fresh frames matter
more than complete history.

## Rule behavior

Three rule types are defined in `config/rules/*.json`:

### `threshold`
Fires on a single frame when a label's confidence is ≥ `confidence_threshold`.

```json
{
  "type": "threshold",
  "pipeline_id": "ppe",
  "label": "NO-Hardhat",
  "confidence_threshold": 0.8,
  "alert_type": "ppe_violation",
  "severity": "HIGH",
  "cooldown_sec": 30.0,
  "draw": true
}
```

### `sustained`
Fires after **N strictly consecutive** frames of detection. Counter resets on any
missed frame. More conservative, prone to false negatives on noisy streams.

```json
{
  "type": "sustained",
  "pipeline_id": "ppe",
  "label": "NO-gloves",
  "confidence_threshold": 0.7,
  "required_consecutive_frames": 5,
  "alert_type": "ppe_sustained_violation",
  "severity": "CRITICAL",
  "cooldown_sec": 60.0,
  "draw": true
}
```

### `window`
Fires when **M of the last N frames** had detection. More robust than `sustained`
because a single missed frame doesn't reset the counter.

```json
{
  "type": "window",
  "pipeline_id": "ppe",
  "label": "NO-glasses",
  "confidence_threshold": 0.7,
  "window_size": 10,
  "min_detections": 6,
  "alert_type": "ppe_persistent_violation",
  "severity": "HIGH",
  "cooldown_sec": 45.0,
  "draw": false
}
```

### Common keys

- `cooldown_sec` — **per-rule** suppression window. Each rule has its own
  `CooldownSuppressor`; cooldowns are independent across rules.
- `draw` — if `true`, this alert's bbox is annotated on the live frame, snapshot,
  and clip. If `false`, raw media is uploaded with no annotation. Defaults to `true`.

## Alert flow

When `processor_task` builds a `FrameResult`:

1. `rule_engine.process(frame_result)` evaluates every rule.
2. If **no alerts fire**:
   - LiveKit (if enabled) gets the **raw frame**.
   - Nothing is uploaded to MinIO or MongoDB.
3. If **at least one alert fires**:
   - LiveKit (if enabled) gets the frame **annotated** with all `draw=true` alert bboxes.
   - Per alert: a snapshot is uploaded (annotated iff `draw=true`).
   - Per alert: a clip is uploaded (each frame in clip is annotated iff `draw=true`).
   - Per alert: an `AlertEvent` is inserted into MongoDB with `alert_id`,
     `snapshot_path`, and `clip_path`.
4. `alert_id` format: `{tenant_id}_{camera_id}_{12-char hex}` for path traceability.

## Adding a new pipeline

1. Create folder: `pipelines/<name>/`
2. Add `pipeline.py` with **two required exports**:
   - A pipeline class with `process(frame_packet) -> PipelineResult` (sync or async).
   - A `build(app_settings) -> <YourPipeline>` factory function.
3. (Optional) Add `pipelines/<name>/config.json`.
4. (Optional) Add a `load_pipeline_config()` helper if your config needs path resolution.

Reference: `pipelines/ppe/pipeline.py` — minimal 50-line `build()`.

The `PipelineManager` discovers it automatically the next time a camera config
includes `<name>` in its `pipelines: [...]` list.

## File map

```
main.py                          Entry point (single-camera worker)
config/
├── rule_loader.py               Load + parse rule JSONs into RuleEngine
├── settings.py                  AppSettings (env-driven Pydantic config)
└── rules/                       Rule configs (default.json + per-camera overrides)
contracts/                       Pydantic data shapes shared across the worker
├── camera_config.py             CameraConfig + SourceType
├── frame_packet.py              FramePacket (auto frame_id)
├── frame_result.py              FrameResult (aggregated PipelineResults)
├── pipeline_result.py           Detection + PipelineResult
└── alert_event.py               AlertEvent (auto alert_id) + severity/status enums
core/
├── frame_buffer.py              Latest-frame-per-camera buffer (LiveKit only)
├── frame_sampler.py             Enforce target_fps
├── pipeline_manager.py          Dynamic pipeline loader, run_pipelines + timeout
├── rule_engine.py               AlertRule classes + CooldownSuppressor + RuleEngine
└── source_reader.py             RTSP / file frame reader with reconnect
livekit/
├── publisher.py                 LiveKit publisher (optional)
└── tokens.py                    LiveKit token generation
pipelines/
├── base_pipeline.py             Abstract BasePipeline
└── ppe/
    ├── pipeline.py              PPE pipeline (Redis Stream → GPU cluster)
    └── config.json              pipeline_id + result_timeout_sec
storage/
├── minio_client.py              MinioSnapshotStore (snapshots + clips)
└── mongo_store.py               MongoAlertStore (camera config fetch + alert insert)
utils/
└── logging.py                   Structured logger setup
redis_stream_sdk/                Used by PPE for Redis Streams
```
