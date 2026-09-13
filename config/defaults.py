"""Editable initial values. Recognition timing requires live calibration."""

SCHEMA_VERSION = 1
ORACLE_KEYS = ("L1", "L2", "L3", "MID", "R1", "R2", "R3")
DEFAULT_ORACLE_LABELS = {
    "L1": "左1", "L2": "左2", "L3": "左奥", "MID": "中央",
    "R1": "右1", "R2": "右2", "R3": "右奥",
}
# Normalized coordinates: layouts can scale without changing identity.
DEFAULT_MAP_POSITIONS = {
    "L1": (0.12, 0.65), "L2": (0.32, 0.40), "L3": (0.50, 0.12),
    "MID": (0.50, 0.65), "R1": (0.50, 0.88),
    "R2": (0.88, 0.65), "R3": (0.70, 0.40),
}
