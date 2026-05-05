# pipeline_manager.py
"""
File Use:
    Dynamically loads and manages pipeline instances.
    Each pipeline module at pipelines/{name}/pipeline.py must expose
    a build(app_settings) factory function.

Implements:
    - PipelineManager

Depends On:
    - pipelines.*/pipeline.build()
    - shared.config.settings

Used By:
    - main.py
"""

import asyncio
import importlib
import inspect
from pathlib import Path

from config.settings import AppSettings
from contracts import FramePacket, PipelineResult
from utils.logging import get_logger

logger = get_logger("pipeline_manager")


class PipelineManager:
    """Dynamically loads only the pipelines requested at startup.

    Adding a new pipeline requires no changes here — just create
    pipelines/{name}/pipeline.py with a build(app_settings) function.
    """

    def __init__(
        self,
        required_pipelines: list[str] | set[str] | None = None,
        app_settings: AppSettings | None = None,
    ) -> None:
        self._app_settings = app_settings or AppSettings()
        self._instances: dict[str, object] = {}

        if required_pipelines:
            for name in required_pipelines:
                self.get_pipeline(name)

    def available_pipelines(self) -> list[str]:
        return sorted(
            p.parent.name
            for p in Path("pipelines").glob("*/pipeline.py")
            if not p.parent.name.startswith("_")
        )

    def loaded_pipelines(self) -> list[str]:
        return list(self._instances.keys())

    def get_pipeline(self, name: str) -> object | None:
        if name in self._instances:
            return self._instances[name]

        try:
            module = importlib.import_module(f"pipelines.{name}.pipeline")
        except ModuleNotFoundError:
            logger.warning("pipeline_module_not_found", pipeline=name)
            return None

        builder = getattr(module, "build", None)
        if builder is None:
            logger.warning("pipeline_missing_build_function", pipeline=name)
            return None

        self._instances[name] = builder(self._app_settings)
        return self._instances[name]

    async def run_pipelines(
        self,
        frame_packet: FramePacket,
        timeout_sec: float | None = None,
    ) -> list[PipelineResult]:
        """Dispatch the frame to every requested pipeline concurrently.

        If timeout_sec is set, pipelines that don't respond in time are
        cancelled and their results are dropped — only completed results
        are returned.
        """
        import time as _time

        tasks: list[asyncio.Task] = []
        task_start: dict[str, float] = {}
        for name in frame_packet.pipelines:
            pipeline = self.get_pipeline(name)
            if pipeline is None:
                logger.warning("pipeline_not_found", pipeline=name)
                continue

            if inspect.iscoroutinefunction(pipeline.process):
                coro = pipeline.process(frame_packet)
            else:
                coro = asyncio.to_thread(pipeline.process, frame_packet)

            task = asyncio.create_task(coro, name=f"pipeline-{name}")
            task_start[task.get_name()] = _time.monotonic()
            tasks.append(task)

        if not tasks:
            return []

        done, pending = await asyncio.wait(tasks, timeout=timeout_sec)

        for task in pending:
            task.cancel()
            elapsed_ms = (_time.monotonic() - task_start[task.get_name()]) * 1000.0
            logger.warning(
                "pipeline_timed_out",
                pipeline=task.get_name(),
                timeout_sec=timeout_sec,
                elapsed_ms=round(elapsed_ms, 1),
                frame_id=frame_packet.frame_id,
            )

        results: list[PipelineResult] = []
        for task in done:
            elapsed_ms = (_time.monotonic() - task_start[task.get_name()]) * 1000.0
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "pipeline_failed",
                    pipeline=task.get_name(),
                    error=str(exc),
                    elapsed_ms=round(elapsed_ms, 1),
                    frame_id=frame_packet.frame_id,
                )
                continue
            logger.debug(
                "pipeline_completed",
                pipeline=task.get_name(),
                elapsed_ms=round(elapsed_ms, 1),
                frame_id=frame_packet.frame_id,
            )
            results.append(task.result())
        return results

    async def close(self) -> None:
        for pipeline in self._instances.values():
            close = getattr(pipeline, "close", None)
            if callable(close):
                try:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
