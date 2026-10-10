"""Polar H10 over Bluetooth LE: packet parsing and the bleak connection.

Two parts:
  * Pure parsers (`parse_hr`, `decode_ecg`) with no I/O, unit tested.
  * `PolarH10`, which runs bleak on its own asyncio thread and reports packets
    through plain callbacks. It imports no Qt; the window wraps the callbacks in
    Qt signals so they arrive on the UI thread.

Every packet is stamped with `time.monotonic()` the moment bleak delivers it,
on the BLE thread, so queueing into the UI thread never skews beat or sample
timing.
"""

from __future__ import annotations

import asyncio
import threading
import time

# Standard Bluetooth Heart Rate Measurement characteristic.
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"

# Polar Measurement Data (PMD) service: control point and data stream.
PMD_SERVICE = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CONTROL = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"

# Start ECG: opcode 02, measurement 00 (ECG), setting 00 (rate) = 130 Hz,
# setting 01 (resolution) = 14 bit. Values are 16-bit little-endian.
ECG_START = bytes([0x02, 0x00, 0x00, 0x01, 0x82, 0x00, 0x01, 0x01, 0x0E, 0x00])
ECG_HZ = 130

NAME_PREFIX = "Polar H10"


# --------------------------------------------------------------------------
# Pure parsers
# --------------------------------------------------------------------------


def parse_hr(data: bytes) -> tuple[bool | None, list[int]]:
    """Parse a Heart Rate Measurement notification.

    Returns (skin_contact, rr_ms). skin_contact is None when the sensor does not
    report contact. RR values arrive in 1/1024 s and are returned as whole ms.

    Flags byte:
      bit 0  HR value is uint16 (else uint8)
      bit 1  skin contact detected
      bit 2  skin contact supported
      bit 3  energy expended present (2 bytes, skipped)
      bit 4  RR intervals present (uint16 each, to the end of the packet)
    """
    flags = data[0]
    i = 3 if flags & 0x01 else 2  # skip flags + HR value
    contact = bool(flags & 0x02) if flags & 0x04 else None
    if flags & 0x08:
        i += 2
    if not flags & 0x10:
        return contact, []
    rr = []
    while i + 1 < len(data):
        ticks = data[i] | (data[i + 1] << 8)
        rr.append(round(ticks * 1000 / 1024))
        i += 2
    return contact, rr


def decode_ecg(data: bytes) -> list[int]:
    """Decode a PMD ECG data frame into microvolt samples.

    Layout: byte 0 measurement type (0x00 = ECG), bytes 1-8 sensor timestamp,
    byte 9 frame type, then 3-byte little-endian signed samples. Frames from any
    other measurement return an empty list.
    """
    if not data or data[0] != 0x00:
        return []
    out = []
    for o in range(10, len(data) - 2, 3):
        out.append(int.from_bytes(data[o : o + 3], "little", signed=True))
    return out


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------


class BluetoothUnavailable(RuntimeError):
    """Bluetooth is off, missing, or the app has no permission to use it."""


class SensorNotFound(RuntimeError):
    """No advertising Polar H10 was found during the scan."""


class PolarH10:
    """One Polar H10 connection on a private asyncio thread.

    Callbacks (all called from the BLE thread; the caller marshals them):
      on_state(state)          "connecting" | "connected" | "disconnected"
      on_hr(arrival, bytes)    Heart Rate Measurement notification
      on_ecg(arrival, bytes)   PMD data notification
      on_ecg_status(ok)        whether the ECG stream started
      on_lost()                link dropped unexpectedly
      on_error(exception)      connect attempt failed
    """

    def __init__(self, on_state, on_hr, on_ecg, on_ecg_status, on_lost, on_error):
        self._on_state = on_state
        self._on_hr = on_hr
        self._on_ecg = on_ecg
        self._on_ecg_status = on_ecg_status
        self._on_lost = on_lost
        self._on_error = on_error
        self._device = None  # remembered so Reconnect reuses the same strap
        self._client = None
        self._closing = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="Polar H10", daemon=True
        )
        self._thread.start()

    # -- public API, safe to call from any thread ---------------------------

    def connect(self, reuse: bool = False) -> None:
        """Scan (unless reusing the last device), connect and start streams."""
        asyncio.run_coroutine_threadsafe(self._connect(reuse), self._loop)

    def close(self, timeout: float = 3.0) -> None:
        """Disconnect cleanly so the strap is free for the next session."""
        self._closing = True
        future = asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
        try:
            future.result(timeout)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)

    # -- BLE thread ---------------------------------------------------------

    async def _connect(self, reuse: bool) -> None:
        try:
            from bleak import BleakClient, BleakScanner
            from bleak.exc import BleakBluetoothNotAvailableError
        except ImportError as exc:  # bleak missing from the environment
            self._on_error(BluetoothUnavailable(str(exc)))
            return

        self._on_state("connecting")
        try:
            await self._disconnect()
            if not reuse or self._device is None:
                try:
                    self._device = await BleakScanner.find_device_by_filter(
                        lambda d, adv: (adv.local_name or d.name or "").startswith(
                            NAME_PREFIX
                        ),
                        timeout=10,
                    )
                except Exception as exc:
                    # A scan only fails when the adapter is off, missing or
                    # not permitted; report it as such.
                    raise BluetoothUnavailable(str(exc)) from exc
                if self._device is None:
                    raise SensorNotFound()

            client = BleakClient(
                self._device, disconnected_callback=self._disconnected, timeout=15
            )
            await client.connect()
            self._client = client

            # Heart rate first: beats keep flowing even if ECG cannot start.
            await client.start_notify(
                HR_MEASUREMENT,
                lambda _, d: self._on_hr(time.monotonic(), bytes(d)),
            )

            # ECG: subscribe to control (for its acknowledgement), then data,
            # then write the start command.
            try:
                await client.start_notify(PMD_CONTROL, lambda *_: None)
                await client.start_notify(
                    PMD_DATA, lambda _, d: self._on_ecg(time.monotonic(), bytes(d))
                )
                await client.write_gatt_char(PMD_CONTROL, ECG_START, response=True)
                self._on_ecg_status(True)
            except Exception:
                self._on_ecg_status(False)

            self._on_state("connected")
        except (BleakBluetoothNotAvailableError, BluetoothUnavailable) as exc:
            self._on_state("disconnected")
            self._on_error(BluetoothUnavailable(str(exc)))
        except Exception as exc:
            await self._disconnect()
            self._on_state("disconnected")
            self._on_error(exc)

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    def _disconnected(self, client) -> None:
        """bleak callback. Only an unexpected drop of the live client counts."""
        if client is not self._client or self._closing:
            return
        self._client = None
        self._on_state("disconnected")
        self._on_lost()
