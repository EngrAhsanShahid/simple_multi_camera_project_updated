import json
from pathlib import Path

from shared.contracts import FramePacket, PipelineResult
from pipelines.dummy.pipeline import DummyPipeline
from pipelines.people_counter.pipeline import PeopleCounterPipeline, load_pipeline_config as load_people_config
from pipelines.ppe.pipeline import PPEPipeline, load_pipeline_config as load_ppe_config
from pipelines.knife.pipeline import knifePipeline, load_pipeline_config as load_knife_config
from pipelines.spiking.pipeline import DrinkingPipeline, load_pipeline_config as load_spiking_config
from pipelines.weapon.pipeline import WeaponPipeline, load_pipeline_config as load_weapon_config

class PipelineManager:
    """Lazy pipeline loader so only requested pipelines are loaded."""

    def __init__(self) -> None:
        self._instances: dict[str, object] = {}
        self._builders = {
            "dummy": self._build_dummy,
            "ppe": self._build_ppe,
            "people_counter": self._build_people_counter,
            "knife": self._build_knife,
            "spiking": self._build_spiking,
            "weapon": self._build_weapon,
        }

    def available_pipelines(self) -> list[str]:
        return sorted(self._builders.keys())

    def _build_dummy(self):
        return DummyPipeline({"pipeline_id": "dummy"})

    def _build_ppe(self):
        config = load_ppe_config(Path("pipelines") / "ppe" / "config.json")
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

    def run_pipelines(self, frame_packet: FramePacket) -> list[PipelineResult]:
        results: list[PipelineResult] = []
        for pipeline_name in frame_packet.pipelines:
            pipeline = self.get_pipeline(pipeline_name)
            if pipeline is None:
                print(f"[WARNING] pipeline not found: {pipeline_name}")
                continue
            result = pipeline.process(frame_packet)
            results.append(result)
        return results
