# pipelines/base_pipeline.py
"""
File Use:
    Abstract base class that every AI pipeline in this worker must implement.
    Defines the lifecycle and contract used by core/pipeline_manager.py.

    Reference implementation: pipelines/ppe/pipeline.py — uses a remote
    Redis Stream + GPU cluster for inference, async setup/process/close,
    config-driven build().

Implements:
    - BasePipeline (abstract pipeline interface)

Depends On:
    - contracts.frame_packet
    - contracts.pipeline_result

Used By:
    - pipelines/ppe/pipeline.py
    - any new pipeline added under pipelines/<name>/pipeline.py

Adding a new pipeline:
    1. Create pipelines/<name>/pipeline.py
    2. Subclass BasePipeline
    3. Implement __init__(config), async process(packet), and (optionally)
       async setup() / async close() for external resources.
    4. Expose a module-level `build(app_settings) -> YourPipeline` factory.
       PipelineManager imports it via importlib — no central registry edit.
"""

from abc import ABC, abstractmethod

from contracts.frame_packet import FramePacket
from contracts.pipeline_result import PipelineResult


class BasePipeline(ABC):
    """Abstract base class every pipeline must inherit from.

    Lifecycle:
        - __init__(config)        : sync, called by build(). Set fields and
                                    config-derived state. DO NOT open network
                                    connections here — defer to setup().
        - async setup()           : optional, called lazily on first process()
                                    or eagerly by callers. Open external
                                    connections (Redis, model loaders, etc).
        - async process(packet)   : called per frame. MUST be awaitable.
                                    MUST NOT raise — return an empty
                                    PipelineResult on failure.
        - async close()           : optional, called on worker shutdown via
                                    PipelineManager.close(). Release any
                                    external resources opened in setup().
        - pipeline_id property    : string identifier carried into every
                                    PipelineResult and used as the dict key
                                    in FrameResult.results.

    Contract notes:
        - CPU-bound inference: offload via asyncio.to_thread, OR (preferred)
          delegate to a remote worker via Redis Stream like PPE does. The
          worker process must stay responsive.
        - Network/model setup: DO NOT do it in __init__. Use setup() so
          PipelineManager construction stays fast and the event loop is
          already running.
        - Errors: catch and log internally. Return PipelineResult with an
          empty detections list. The processor task does not handle exceptions
          per-pipeline — a raised exception kills the whole frame.
    """

    @abstractmethod
    def __init__(self, config: dict) -> None:
        """Initialize pipeline state from config.

        Args:
            config: Pipeline-specific dict, typically loaded from
                    pipelines/<name>/config.json by the build() factory.
                    May contain values injected by build() from AppSettings
                    (e.g., PPE injects redis_url here).

        DO NOT open network connections, load models, or do other I/O here.
        Defer those to setup().
        """

    @abstractmethod
    async def process(self, frame_packet: FramePacket) -> PipelineResult:
        """Process a single frame and return detections.

        Args:
            frame_packet: Frame + metadata. The frame is a numpy array.

        Returns:
            PipelineResult with detections. MUST include the same
            tenant_id / camera_id / frame_id / timestamp / pipeline_id
            as the input packet (PPE shows the canonical pattern).

        Contract:
            - MUST be awaitable (async def).
            - MUST NOT raise. Catch exceptions internally, log them,
              return PipelineResult(detections=[]).
            - SHOULD enforce its own internal timeout for any remote
              operations (e.g., PPE uses asyncio.wait_for on Redis results).
            - SHOULD record inference_time_ms on the result for observability.
        """

    @property
    @abstractmethod
    def pipeline_id(self) -> str:
        """Stable string identifier for this pipeline (e.g. 'ppe', 'knife').

        Must match the value used in CameraConfig.pipelines and rule
        config 'pipeline_id' fields.
        """

    async def setup(self) -> None:
        """Open external resources (Redis, model files, etc).

        Default: no-op. Override if your pipeline needs async initialization.
        Called lazily by process() on first use, or eagerly by callers.
        Must be safe to call multiple times (idempotent — guard with a flag).
        """
        return None

    async def close(self) -> None:
        """Release external resources opened in setup().

        Default: no-op. Override if your pipeline owns connections.
        Called by PipelineManager.close() during graceful shutdown.
        Must not raise.
        """
        return None
