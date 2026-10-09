"""Polar H10 recording and offline session review.

Data flow: protocol decodes BLE packets; engine timestamps and records them;
storage commits raw chunks; analysis and respiration read saved observations.
ui owns the Qt application, and dashboard presents finished-session results.
"""
