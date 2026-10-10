"""Sonorium session desk for instrumented sound-therapy sessions.

Data flow:
  ble.py        Polar H10 over Bluetooth LE: connect, parse HR/RR and ECG packets
  session.py    live session store: beats, ECG, label events, recording phase
  hrv.py        artifact detection, RMSSD, signal quality (pure maths)
  documents.py  attached consent/questionnaire files and ID auto-fill
  export.py     beats CSV, ECG CSV, session JSON, session package (.zip)
  config.py     label palette, hotkeys, HRV windows, ID patterns
  ui/           PySide6 window, charts, dialogs
  registry.py   shared study registry used by the survey app (svaram-data)
"""
