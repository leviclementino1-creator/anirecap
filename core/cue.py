from dataclasses import dataclass


@dataclass
class Cue:
    """Uma fala de legenda com tempos em segundos."""
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)
