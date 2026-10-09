"""Versioned sound-state vocabulary shared by recording and analysis."""

SOUND_ELEMENTS = ("fire", "water", "wind", "earth", "space")
SOUND_VALUES = ("silence", *SOUND_ELEMENTS)
LABEL_SCHEMA_VERSION = 1
SOUND_COLORS = {
    "silence": "#839088", "fire": "#b65a40", "water": "#4d7891",
    "wind": "#71978f", "earth": "#947044", "space": "#82759e",
}
