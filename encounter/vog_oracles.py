"""Stable encounter identities, independent of user-facing labels."""
from enum import Enum


class OracleId(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    MID = "MID"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
