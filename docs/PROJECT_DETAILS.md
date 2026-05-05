# Project Details

This project is a **single-camera AI processing worker**. One worker process is bound
to one camera (one `(tenant_id, camera_id)` pair) and runs end-to-end:
read frames → run AI pipelines (with timeout) → evaluate alert rules → persist alerts
and media → optionally publish to LiveKit.

## What this version is

- **Single camera per process**: deploy one worker per camera you want to monitor.
- **Camera config from MongoDB**: no static `camera.json` — `(tenant_id, camera_id)` is
  passed via CLI, the full config is loaded from MongoDB at startup and validated.
- **Remote inference**: the active PPE pipeline publishes frames to a Redis Stream;
  a separate GPU cluster consumes the stream, runs YOLO, and pushes results back.
  The pipeline only handles serialization, async waiting, and post-processing.
- **Alert-driven annotation**: live stream, snapshots, and clips are only annotated
  when an alert fires *and* the firing rule has `draw: true`. Otherwise the media
  is sent raw — no green clutter from non-alerting detections.
- **Pluggable pipelines via convention**: any folder under `pipelines/<name>/`
  exposing a `build(app_settings)` function is auto-loadable. No central registry,
  no hardcoded imports in `pipeline_manager.py`.
- **Per-rule cooldown + draw**: each rule owns its own `CooldownSuppressor` and
  `draw` flag. Two rules on the same label can have different suppression windows
  and different drawing behavior.
- **Three rule types**: `threshold`, `sustained` (N consecutive frames),
  and `window` (M of last N frames).

## What was removed compared to earlier drafts

The codebase was originally scaffolded as a multi-service platform (API service,
ingest router, pipeline runner, event processor, multi-camera orchestrator). All of
that was deleted. What remains is the worker only.

Specifically removed:
- `apps/api_service/`, `apps/ingest_router/`, `apps/pipeline_runner/`
- `shared/auth/`, `shared/audit/`, `shared/metrics/`, `shared/media/`, `shared/messaging/`
- Legacy pipelines: `dummy`, `knife`, `weapon`, `spiking`, `people_counter`
- `aggregator.py` and `alert_engine.py` (replaced by inline FrameResult building
  in `processor_task` and `RuleEngine.process()`)
- Multi-camera orchestration in `main.py` (`--camera_ids` plural is now `--camera_id` singular)
- `frame_id`/`alert_id` are now prefixed `{tenant_id}_{camera_id}_<hex>` for traceability

## Why pipelines are dynamically loaded

`PipelineManager.get_pipeline(name)` does:

```python
module = importlib.import_module(f"pipelines.{name}.pipeline")
module.build(self._app_settings)
```

Adding a new pipeline = create `pipelines/<name>/pipeline.py` with a `build(app_settings)`
factory function and (optionally) a `config.json`. **Zero changes** to `pipeline_manager.py`.
Each camera's `pipelines: [...]` list determines which factories actually run.

## Why timeouts matter

- **Outer timeout** (`AGGREGATOR_TIMEOUT_MS` in `.env`): `pipeline_manager.run_pipelines()`
  caps how long the processor waits for any pipeline. Stragglers are cancelled
  via `asyncio.wait(..., timeout=...)`. This bounds the per-frame latency.
- **PPE-internal timeout** (`result_timeout_sec` in `pipelines/ppe/config.json`):
  PPE waits this long for the GPU cluster to push results back via Redis. On timeout
  it returns an empty `PipelineResult` and logs `ppe_result_timeout`.

If either timeout fires, the dropped result is logged so you can see how often
the worker is falling behind.

## Why frames stay bounded

Three buffers, all bounded:

| Buffer | Type | Bound |
|---|---|---|
| `camera_ring_buffers[cam]` | deque (raw frames at full FPS) | `maxlen = ALERT_FRAMES_BEFORE + ALERT_FRAMES_AFTER + 1` |
| `asyncio.Queue` reader→processor | FramePackets at sampled FPS | `maxsize = QUEUE_MAXSIZE`, `put_latest` evicts oldest |
| `frame_buffer` (LiveKit) | latest frame per camera_id | single slot per camera, overwritten |

When the queue is full, the dropped FramePacket is logged as `frame_dropped_queue_full`.

## Limitations

- **One process = one camera.** To monitor N cameras, run N worker processes
  (each with its own `--camera_id`). There is no in-process multi-camera orchestrator.
- **MongoDB is required** at startup to fetch the camera config and at runtime to
  insert alerts. There is no fallback.
- **MinIO is required** for snapshot and clip storage. There is no local fallback.
- **Redis cluster + GPU consumer is required** for PPE inference. The pipeline
  itself does not run YOLO locally.
- **Daemon clip threads** (`save_alert_clip`) can be killed mid-write at process
  shutdown. Not a data-loss concern for the alert metadata (which is written
  synchronously to MongoDB), only the clip media may be incomplete.

## Configuration entry points

- `.env` → `AppSettings` (Pydantic) at `config/settings.py`
- `config/rules/default.json` — fallback alert rules
- `config/rules/{camera_id}.json` — optional per-camera rule overrides
- `pipelines/<name>/config.json` — per-pipeline static config
- MongoDB `cameras` collection — per-camera runtime config (source path, target FPS,
  pipelines, enabled flag, record_full_video flag)
