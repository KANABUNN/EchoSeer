"""Encounter rules are data, separate from audio classification."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EncounterDefinition:
    name: str
    sequence_lengths: tuple[int, ...]

    def __post_init__(self) -> None:
        lengths = tuple(self.sequence_lengths)
        if not self.name or not lengths or len(lengths) > 20 or any(type(n) is not int or not 1 <= n <= 7 for n in lengths):
            raise ValueError("Invalid encounter definition")
        object.__setattr__(self, "sequence_lengths", lengths)

    def expected_count(self, round_index: int) -> int:
        if type(round_index) is not int or not 1 <= round_index <= len(self.sequence_lengths):
            raise ValueError("Round is outside this encounter")
        return self.sequence_lengths[round_index - 1]


VOG_ORACLES = EncounterDefinition("vog_oracles", (3, 4, 5, 6, 7))
