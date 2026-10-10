"""Study configuration in one place: label palette, HRV windows and ID patterns.

Edit this file to change the instruments, their hotkeys or the participant ID
scheme. Nothing else in the app hard-codes these values.
"""

import re

# Label palette. Each family has a colour token (see ui/theme.py) and a list of
# (label, hotkey) pairs. "Silence" is the control state: it is recorded whenever
# no instrument is on, and every recording starts in it.
FAMILIES = [
    {"name": "Control", "color": "f-silence", "items": [("Silence", "0")]},
    {
        "name": "Gongs and metal",
        "color": "f-metal",
        "items": [
            ("Plate Gong", "1"),
            ("Plate Bells", "2"),
            ("Tubular Bells", "3"),
            ("Spiral Chime", "4"),
            ("Tubular Resonators", "5"),
            ("Angel Chimes", "6"),
        ],
    },
    {
        "name": "Drums and rattles",
        "color": "f-drum",
        "items": [
            ("Storm Drum", "Q"),
            ("Shamanic Drum", "W"),
            ("Ocean Drum", "E"),
            ("Rattles", "R"),
        ],
    },
    {
        "name": "Stone, glass, water",
        "color": "f-earth",
        "items": [("Sound Stone", "A"), ("Glassophone", "S"), ("Water Bowl", "D")],
    },
    {
        "name": "Bed, breath, voice",
        "color": "f-breath",
        "items": [
            ("Sound Bed", "Z"),
            ("Flutes", "X"),
            ("Wind Breath", "C"),
            ("Natural Voice", "V"),
        ],
    },
]

SILENCE = "Silence"

# Derived lookups: label -> {"key", "color"} and hotkey -> label.
LABELS = {
    name: {"key": key, "color": family["color"]}
    for family in FAMILIES
    for name, key in family["items"]
}
HOTKEYS = {info["key"]: name for name, info in LABELS.items()}

# Rolling RMSSD windows in seconds. The first (smallest) is the default.
HRV_WINDOWS = (15, 30, 45)

# Trend chart time spans in seconds; 0 means the whole recording.
VIEW_WINDOWS = (("5 min", 300), ("15 min", 900), ("All", 0))

# ID auto-fill. ID_PATTERNS find IDs in file names and document text.
# ID_FIELDS recognise a field name (CSV header, JSON key) for each ID, which must
# also contain an id/code/number word (ID_WORD), e.g. "participant_id".
ID_PATTERNS = {
    "p": re.compile(r"\bPT-?\d{2,6}\b", re.I),
    "s": re.compile(r"\bS-\d[\d-]{1,12}\d\b", re.I),
    "f": re.compile(r"\bFC-?\d{1,4}\b", re.I),
}
ID_FIELDS = {
    "p": re.compile(r"participant", re.I),
    "s": re.compile(r"session", re.I),
    "f": re.compile(r"facilitator", re.I),
}
ID_WORD = re.compile(r"id\b|_id|code|number", re.I)

# Accepted document types for the New recording dialog (Qt file-dialog filters).
DOC_FILTERS = {
    "consent": "Consent form (*.pdf *.png *.jpg *.jpeg *.heic *.tif *.tiff)",
    "questionnaire": (
        "Questionnaire (*.pdf *.csv *.json *.xlsx *.xls *.txt"
        " *.png *.jpg *.jpeg *.heic *.tif *.tiff)"
    ),
}
