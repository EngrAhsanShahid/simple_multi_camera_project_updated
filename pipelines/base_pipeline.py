# pipelines/base_pipeline.py
"""
File Use:
    Defines the abstract base class that all AI pipelines must implement.
    Enforces a consistent interface for the Pipeline Runner to manage
    pipelines as interchangeable plug-in modules.

Implements:
    - BasePipeline (abstract pipeline interface)

Depends On:
    - shared.contracts.frame_packet
    - shared.contracts.pipeline_result

Used By:
    - pipelines/ppe/pipeline.py
    - pipelines/fight/pipeline.py
    - apps/pipeline_runner/process_manager.py
"""

from abc import ABC, abstractmethod

from shared.contracts.frame_packet import FramePacket
from shared.contracts.pipeline_result import PipelineResult


class BasePipeline(ABC):
    """Base interface all AI pipelines must implement.

    Responsibilities:
        - Define the contract between Pipeline Runner and pipeline implementations.
        - Enforce model loading at init time (not per-frame).
        - Ensure pipelines are self-contained and pipeline-independent.
    """

    @abstractmethod
    def __init__(self, config: dict) -> None:
        """Load models and initialize pipeline resources.

        Args:
            config: Pipeline-specific configuration dictionary.

        Model loading MUST happen here, not inside the processing loop.
        """

    @abstractmethod
    def process(self, frame_packet: FramePacket) -> PipelineResult:
        """Process a single frame and return detections.

        Args:
            frame_packet: The frame and metadata to process.

        Returns:
            PipelineResult with detections. Must never raise — return
            an empty PipelineResult on failure.
        """
