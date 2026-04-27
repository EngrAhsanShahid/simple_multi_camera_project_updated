import asyncio
import inspect
from pathlib import Path

from shared.config.settings import AppSettings
from shared.contracts import FramePacket, PipelineResult
from pipelines.dummy.pipeline import DummyPipeline
from pipelines.people_counter.pipeline import PeopleCounterPipeline, load_pipeline_config as load_people_config
from pipelines.ppe.pipeline import PPEPipeline, load_pipeline_config as load_ppe_config
from pipelines.knife.pipeline import knifePipeline, load_pipeline_config as load_knife_config
from pipelines.spiking.pipeline import DrinkingPipeline, load_pipeline_config as load_spiking_config
from pipelines.weapon.pipeline import WeaponPipeline, load_pipeline_config as load_weapon_config


class PipelineManager:
    """Pre-loads only requested pipelines during initialization.

    Accepts an AppSettings instance so pipeline configs (e.g. PPE's Redis URL)
    can be sourced from a single, env-backed configuration layer.
    """

    def __init__(
        self,
        required_pipelines: list[str] | set[str] | None = None,
        app_settings: AppSettings | None = None,
    ) -> None:
        self._app_settings = app_settings or AppSettings()
        self._instances: dict[str, object] = {}
        self._builders = {
            "dummy": self._build_dummy,
            "ppe": self._build_ppe,
            "people_counter": self._build_people_counter,
            "knife": self._build_knife,
            "spiking": self._build_spiking,
            "weapon": self._build_weapon,
        }

        # Pre-initialize only the required pipelines
        if required_pipelines:
            for pipeline_name in required_pipelines:
                self.get_pipeline(pipeline_name)

    def available_pipelines(self) -> list[str]:
        return sorted(self._builders.keys())

    def loaded_pipelines(self) -> list[str]:
        return list(self._instances.keys())

    def _build_dummy(self):
        return DummyPipeline({"pipeline_id": "dummy"})

    def _build_ppe(self):
        config = load_ppe_config(Path("pipelines") / "ppe" / "config.json")
        # Inject Redis URL from central AppSettings so operators configure it via .env
        config.setdefault("redis_url", self._app_settings.redis.url)
        return PPEPipeline(config)

    def _build_people_counter(self):
        config = load_people_config(Path("pipelines") / "people_counter" / "config.json")
        return PeopleCounterPipeline(config)

    def _build_knife(self):
        config = load_knife_config(Path("pipelines") / "knife" / "config.json")
        return knifePipeline(config)

    def _build_spiking(self):
        config = load_spiking_config(Path("pipelines") / "spiking" / "config.json")
        return DrinkingPipeline(config)

    def _build_weapon(self):
        config = load_weapon_config(Path("pipelines") / "weapon" / "config.json")
        return WeaponPipeline(config)

    def get_pipeline(self, pipeline_name: str):
        if pipeline_name not in self._instances:
            builder = self._builders.get(pipeline_name)
            if builder is None:
                return None
            self._instances[pipeline_name] = builder()
        return self._instances[pipeline_name]

    async def run_pipelines(self, frame_packet: FramePacket) -> list[PipelineResult]:
        """Dispatch the frame to every requested pipeline concurrently.

        PPE is the async baseline — its `process` is a coroutine and runs
        on the event loop. Legacy sync pipelines are offloaded via
        `asyncio.to_thread` so their CPU-bound inference does not block
        camera readers and frontend publishers.
        """
        tasks = []
        for pipeline_name in frame_packet.pipelines:
            pipeline = self.get_pipeline(pipeline_name)
            if pipeline is None:
                print(f"[WARNING] pipeline not found: {pipeline_name}")
                continue

            if inspect.iscoroutinefunction(pipeline.process):
                tasks.append(pipeline.process(frame_packet))
            else:
                tasks.append(asyncio.to_thread(pipeline.process, frame_packet))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks)
        return list(results)

    async def close(self) -> None:
        for pipeline in self._instances.values():
            close = getattr(pipeline, "close", None)
            if callable(close):
                try:
                    maybe_result = close()
                    if asyncio.iscoroutine(maybe_result):
                        await maybe_result
                except Exception:
                    pass
