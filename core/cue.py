from dataclasses import dataclass, field


@dataclass
class Cue:
    """Uma fala de legenda com tempos em segundos.

    `speaker` é opcional — vem do campo `Name` do .ass quando o ripper
    preencheu (alguns sim, muitos não). Quando disponível, é injetado no
    transcript que vai pro LLM resumidor pra evitar atribuição errada de
    falas em cenas com 3+ personagens.
    """
    start: float
    end: float
    text: str
    speaker: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)
