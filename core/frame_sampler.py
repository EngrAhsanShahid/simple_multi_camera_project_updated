import time


class FrameSampler:
    def __init__(self, target_fps: float) -> None:
        self.target_fps = target_fps
        self.interval = 1.0 / target_fps if target_fps > 0 else 0.0
        self.last_sample_time = 0.0

    def should_sample(self) -> bool:
        now = time.monotonic()
        if now - self.last_sample_time >= self.interval:
            self.last_sample_time = now
            return True
        return False
