# Project Details

## What was added in this version

1. Added **PPE pipeline** from the original Nexa project
2. Added **people counter pipeline** from the original Nexa project
3. Kept the original **dummy pipeline** for testing without AI
4. Added project manual and architecture diagram
5. Updated camera records in MongoDB to use `source_path` instead of separate `file_path`/`rtsp_url`
6. Updated `requirements.txt` to include `ultralytics`
7. Updated `pipeline_manager.py` to load pipelines lazily

## Why lazy loading is useful

AI models are large. With lazy loading:
- the model is loaded only when needed
- unused models do not slow startup
- errors stay isolated to the pipeline that failed

## Limitations

- MongoDB must be available if you want to save alerts.
- PPE works only if `ultralytics` is installed.
- People counter also needs YOLO weights. The default path is `yolov8n.pt`.

## Beginner note

If you want to test only the architecture first, set pipelines to:

```json
["dummy"]
```

After that, move to:

```json
["ppe"]
```

and later:

```json
["ppe", "people_counter"]
```
