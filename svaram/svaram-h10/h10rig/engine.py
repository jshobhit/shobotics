"""Acquisition lives on its own asyncio thread, independent of the Qt display."""

from __future__ import annotations

import asyncio
from collections import deque
import math
from pathlib import Path
import threading
import time
import uuid

from .protocol import (
    PMD_CONTROL,
    PMD_DATA,
    HR,
    BATTERY,
    decode_pmd,
    decode_hr,
    parse_settings,
    start_command,
    SensorClock,
)
from .storage import SessionWriter, atomic_json
from .respiration import WindowProcessor


class Engine:
    """Own sensor acquisition and recording on a dedicated asyncio thread.

    The Qt thread submits coroutines and reads a locked state snapshot.
    Sensor callbacks, storage writes, and housekeeping run on this loop.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.state = dict(
            connection="Disconnected",
            recording=False,
            device="",
            battery=None,
            hr=None,
            respiration=None,
            resp_status="Needs 60 seconds of saved ECG",
            quality="Connect a sensor to check signal",
            write="No active session",
            duration=0,
            dropout=False,
            session="",
            error="",
            simulated=False,
            ready=False,
            ecg=[],
            motion=0,
            sound_label="silence",
        )

        # The display buffer is bounded; complete raw observations go to SessionWriter.
        self.preview = deque(maxlen=130 * 8)
        self.client = None
        self.session = None
        self.registry = None
        self.device = None
        self.want_connection = False
        self.simulated = False
        self.loop = asyncio.new_event_loop()
        self.clock = SensorClock()
        self.last_received = {}
        self.quality_state = {}
        self.stream_started = []
        self.cp_queue = None
        self.reconnect_task = None
        self.sim_task = None
        self.house_task = None
        self.writer_failed = False
        self.processor = WindowProcessor()
        self.live_seq = 0
        self.thread = threading.Thread(
            target=self._run, name="H10 acquisition", daemon=True
        )
        self.thread.start()

    def _run(self):
        """Run acquisition until shutdown, then cancel and drain remaining tasks."""
        asyncio.set_event_loop(self.loop)
        self.house_task = self.loop.create_task(self.housekeeping())
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    def update(self, **values):
        """Publish display-state changes under the lock shared with the Qt thread."""
        with self.lock:
            self.state.update(values)

    def snapshot(self):
        """Copy display state so the UI can read it outside the acquisition thread."""
        with self.lock:
            return dict(self.state)

    def submit(self, coro):
        """Schedule a coroutine from the UI thread and return its future."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def relative(self, mono=None):
        """Convert a host monotonic time to seconds since recording began."""
        return (time.monotonic() if mono is None else mono) - (
            self.session.started_mono if self.session else 0
        )

    def event(self, category, kind, **detail):
        if self.session and not self.writer_failed:
            self.session.event(category, kind, self.relative(), **detail)

    def quality(self, kind, active, **detail):
        """Log quality transitions once instead of repeating each active warning."""
        if self.quality_state.get(kind) != active:
            self.quality_state[kind] = active
            if active or kind in self.quality_state:
                self.event("quality", kind, active=active, **detail)

    async def scan(self):
        if self.want_connection or self.session:
            raise RuntimeError("Disconnect before scanning")
        self.update(connection="Scanning…", error="")
        try:
            from bleak import BleakScanner

            devices = await BleakScanner.discover(timeout=6, return_adv=True)
            return [
                (d, adv.local_name or d.name or "Polar H10")
                for d, adv in devices.values()
                if "polar h10" in (adv.local_name or d.name or "").lower()
            ]
        finally:
            self.update(connection="Disconnected")

    async def connect(self, device=None, simulated=False):
        if self.want_connection or self.session:
            raise RuntimeError("Stop/disconnect the current sensor first")
        self.device = device
        self.simulated = simulated
        self.want_connection = True
        self.writer_failed = False
        self.preview.clear()
        self.last_received.clear()
        self.quality_state.clear()
        self.update(
            error="",
            hr=None,
            respiration=None,
            battery=None,
            simulated=simulated,
            device=(
                "SIMULATOR · no physiological measurements"
                if simulated
                else (device.name or device.address)
            ),
        )
        try:
            if simulated:
                self.clock.reset()
                self.update(
                    connection="Connected · simulator", battery=100, dropout=False
                )
                self.sim_task = asyncio.create_task(self.simulate())
            else:
                await self.connect_real()
        except Exception:
            self.want_connection = False
            await self.disconnect_transport()
            self.update(connection="Disconnected", ready=False)
            raise

    async def command(self, data):
        """Send a PMD command and collect its matching, possibly multipart reply."""

        # Discard stale replies before waiting for this command's acknowledgement.
        while not self.cp_queue.empty():
            self.cp_queue.get_nowait()
        await self.client.write_gatt_char(PMD_CONTROL, data, response=True)
        parameters = bytearray()
        deadline = self.loop.time() + 8
        while True:
            response = await asyncio.wait_for(
                self.cp_queue.get(), max(0.01, deadline - self.loop.time())
            )
            if len(response) < 4 or response[0] != 0xF0 or response[1:3] != data[:2]:
                continue
            if response[3] != 0:
                raise RuntimeError(
                    f"H10 rejected command {data[:2].hex()}: status {response[3]}. Close other sensor apps and reconnect."
                )
            if len(response) > 5:
                parameters.extend(response[5:])
            if len(response) < 5 or response[4] == 0:
                return bytes(parameters)

    async def connect_real(self):
        """Subscribe to BLE notifications, then start ECG and motion streams."""
        from bleak import BleakClient

        self.update(connection="Connecting…", ready=False)
        self.clock.reset()
        self.last_received.clear()
        self.stream_started = []
        client = BleakClient(
            self.device, disconnected_callback=self.disconnected, timeout=15
        )
        self.client = client
        await client.connect()
        self.cp_queue = asyncio.Queue()
        await client.start_notify(
            PMD_CONTROL, lambda _, d: self.cp_queue.put_nowait(bytes(d))
        )
        await client.start_notify(PMD_DATA, self.on_pmd)
        await client.start_notify(HR, self.on_hr)
        for kind in (0, 2):
            settings = parse_settings(await self.command(bytes([1, kind])))
            response = await self.command(start_command(kind, settings))
            self.stream_started.append(kind)
        try:
            battery = await client.read_gatt_char(BATTERY)
            self.update(battery=int(battery[0]))
        except Exception:
            self.update(battery=None)
        self.update(connection="Connected", dropout=False, error="")
        self.event("connection", "connected", device_id=self.device.address)

    def disconnected(self, client):
        if client is not self.client or not self.want_connection:
            return
        self.update(
            connection="Reconnecting…",
            dropout=True,
            ready=False,
            hr=None,
            respiration=None,
        )
        try:
            self.event("connection", "disconnected")
            self.quality("bluetooth_dropout", True)
            if self.session:
                self.session.flush(True)
        except Exception as exc:
            self.fail_storage(exc)
        if not self.reconnect_task or self.reconnect_task.done():
            self.reconnect_task = asyncio.create_task(self.reconnect())

    async def reconnect(self):
        """Retry the sensor connection with delays capped at ten seconds."""
        delay = 1
        while self.want_connection and not self.writer_failed:
            await asyncio.sleep(delay)
            try:
                await self.disconnect_transport()
                await self.connect_real()
                if not self.want_connection:
                    return
                self.event("connection", "reconnected")
                self.quality("bluetooth_dropout", False)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.update(
                    connection=f"Reconnecting… next try in {min(delay*2,10)}s",
                    error=str(exc),
                )
                delay = min(delay * 2, 10)

    async def disconnect_transport(self):
        client = self.client
        self.client = None
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    def packet(self, kind, data, receipt):
        """Retain original notification bytes before attempting to decode them."""
        if self.session and not self.writer_failed:
            self.session.add(
                "packets",
                [
                    dict(
                        t=self.relative(receipt),
                        host_monotonic_s=receipt,
                        characteristic=kind,
                        hex=bytes(data).hex(),
                    )
                ],
            )

    def on_pmd(self, _, data):
        """Decode ECG/motion, attach sample timestamps, and forward observations."""
        receipt = time.monotonic()
        try:
            self.packet("PMD", data, receipt)
            stream, stamp, rows = decode_pmd(bytes(data))
            hz = 130 if stream == "ecg" else 50
            times, stamps, events = self.clock.map(
                stream, stamp, len(rows), hz, receipt
            )
            for kind, detail in events:
                self.event(
                    "quality" if kind != "clock_anchor" else "connection",
                    kind,
                    **detail,
                )
            for row, t, s in zip(rows, times, stamps):
                row.update(
                    t=self.relative(t),
                    sensor_ns=s,
                    host_receipt_monotonic_s=receipt,
                    clock_generation=self.clock.generation,
                )
            self.receive(stream, rows, receipt, times)
        except ValueError as exc:
            self.update(error=str(exc), quality="Unsupported or malformed sensor data")
            try:
                self.event(
                    "quality",
                    "packet_decode_error",
                    message=str(exc),
                    packet_hex=bytes(data).hex(),
                )
            except Exception as error:
                self.fail_storage(error)
        except Exception as exc:
            self.fail_storage(exc)

    def on_hr(self, _, data):
        """Save reported heart rate and reconstruct RR beat endpoints from receipt."""
        receipt = time.monotonic()
        try:
            self.packet("HR", data, receipt)
            result = decode_hr(bytes(data))
            self.update(hr=result["bpm"])
            self.last_received["hr"] = receipt
            self.quality(
                "poor_electrode_contact",
                result["contact"] is False,
                source="H10 HR contact flag",
            )
            if self.session and not self.writer_failed:
                self.session.add(
                    "hr",
                    [
                        dict(
                            t=self.relative(receipt),
                            bpm=result["bpm"],
                            flags=result["flags"],
                            contact=result["contact"],
                            energy_kj=result["energy_kj"],
                            host_receipt_monotonic_s=receipt,
                        )
                    ],
                )
                ticks = result["rr_ticks"]
                end = self.relative(receipt)
                rr = []

                # The last beat is anchored to receipt; earlier endpoints step backwards by RR.
                for value in reversed(ticks):
                    rr.append(
                        dict(
                            t=end,
                            rr_ticks=value,
                            rr_ms=value * 1000 / 1024,
                            timestamp_method="beat_endpoint_reconstructed_from_receipt",
                            host_receipt_monotonic_s=receipt,
                        )
                    )
                    end -= value / 1024
                self.session.add("rr", list(reversed(rr)))
        except ValueError as exc:
            self.update(error=str(exc))
            try:
                self.event("quality", "hr_decode_error", message=str(exc))
            except Exception as error:
                self.fail_storage(error)
        except Exception as exc:
            self.fail_storage(exc)

    def receive(self, stream, rows, receipt, times=None):
        """Save incoming rows and update the live trace or motion quality flag."""
        self.last_received[stream] = receipt
        if self.session and not self.writer_failed:
            self.session.add(stream, rows)
        if stream == "ecg":
            if times is None:
                times = [receipt - (len(rows) - 1 - i) / 130 for i in range(len(rows))]
            self.preview.extend((t, r["uv"]) for t, r in zip(times, rows))
            vals = [x[1] for x in list(self.preview)[-260:]]
            if len(vals) >= 130:
                self.quality(
                    "flat_or_clipped_ecg",
                    max(vals) - min(vals) < 40 or max(map(abs, vals)) > 30000,
                )
        else:
            motion = max(
                abs(math.sqrt(r["x_mg"] ** 2 + r["y_mg"] ** 2 + r["z_mg"] ** 2) - 1000)
                for r in rows
            )
            self.update(motion=round(motion))
            self.quality("excessive_motion", motion > 450)

    def fail_storage(self, exc):
        """Stop acquisition and leave the recording incomplete for recovery."""
        self.writer_failed = True
        self.want_connection = False
        self.update(
            error=f"Recording interrupted: {exc}. Committed data is preserved; recover this session.",
            write="WRITE ERROR · session incomplete",
            recording=False,
            ready=False,
            connection="Stopped after recording error",
        )
        if self.session:
            try:
                self.session.manifest.update(status="incomplete", failure=str(exc))
                self.session.save_manifest()
            except Exception:
                pass
        asyncio.create_task(self.disconnect_transport())

    async def start(self, participant="", study_session_id=None, registry=None):
        """Create session storage once both ECG and motion are arriving."""
        if self.session:
            raise RuntimeError("A session already exists; stop or recover it first")
        if not self.snapshot()["ready"]:
            raise RuntimeError("Wait for live ECG and motion data before starting")
        device = "SIMULATOR" if self.simulated else self.device.address
        study = None
        if registry is not None:
            study = await asyncio.to_thread(registry.reserve_recording, study_session_id, device, self.simulated)
        elif not self.simulated:
            raise ValueError("Select a study session from the shared registry first")
        try:
            self.session = SessionWriter(
                study["root"] if study else self.root,
                device,
                study["participant_code"] if study else participant,
                simulated=self.simulated,
                study=study,
            )
        except Exception:
            if study:
                await asyncio.to_thread(registry.abandon_reservation, study["recording_id"])
            raise
        self.registry = registry
        self.writer_failed = False
        self.processor = WindowProcessor()
        self.live_seq = 0
        for kind, active in self.quality_state.items():
            if active:
                self.event("quality", kind, active=True, at_recording_start=True)
        if self.clock.anchor_sensor is not None:
            self.event(
                "connection",
                "clock_anchor",
                sensor_ns=self.clock.anchor_sensor,
                host_monotonic_s=self.clock.anchor_host,
                generation=self.clock.generation,
            )
        self.update(
            recording=True,
            sound_label="silence",
            session=str(self.session.path),
            write="Recording · first save within 5s",
            respiration=None,
            resp_status="Needs 60 seconds of saved ECG",
            error="",
        )
        return str(self.session.path)

    async def set_sound_label(self, value, requested_mono=None):
        """Serialize label changes with acquisition using the button-press clock."""
        if not self.session or not self.snapshot()["recording"] or self.writer_failed:
            raise ValueError("Start recording before setting a sound label")
        t = self.relative(requested_mono)
        try:
            saved = self.session.set_sound_label(value, t)
        except OSError as exc:
            self.fail_storage(exc)
            raise
        self.update(sound_label=saved)
        return saved

    async def mark_phase(self, phase):
        if phase not in ("rest", "sound", "recovery"):
            raise ValueError("Unknown study phase")
        if not self.session or not self.snapshot()["recording"]:
            raise ValueError("Start recording before adding a phase mark")
        try:
            self.session.event("study", "phase", self.session.clock() - self.session.started_mono,
                               id=str(uuid.uuid4()), phase=phase)
        except OSError as exc:
            self.fail_storage(exc)
            raise
        return phase

    async def housekeeping(self):
        """Refresh signal health, flush storage, and derive saved ECG windows.

        This task keeps recording independently of the Qt display timer.
        """
        while True:
            await asyncio.sleep(0.2)
            now = time.monotonic()
            try:
                live = all(
                    now - self.last_received.get(k, 0) < 3
                    for k in ("ecg", "accelerometer")
                )
                ready = (
                    self.want_connection
                    and live
                    and not self.snapshot()["dropout"]
                    and not self.writer_failed
                )
                if self.want_connection:
                    for k in ("ecg", "accelerometer"):
                        if k in self.last_received:
                            self.quality(
                                "missing_" + k, now - self.last_received[k] > 3
                            )
                    if now - self.last_received.get("hr", now) > 5:
                        self.update(hr=None)
                active = [
                    k.replace("_", " ") for k, v in self.quality_state.items() if v
                ]
                quality = (
                    " · ".join(active)
                    if active
                    else (
                        "Signal active · inspect trace"
                        if live
                        else "Waiting for ECG and motion"
                    )
                )
                self.update(ready=ready, ecg=list(self.preview), quality=quality)
                if self.session and not self.writer_failed:

                    # Only committed ECG is passed to the respiration processor.
                    rows = self.session.flush()
                    if rows:
                        for result in self.processor.feed(rows):
                            atomic_json(
                                self.session.path
                                / "derived"
                                / "respiration"
                                / f"live_{self.live_seq:06d}.json",
                                result,
                                exclusive=True,
                            )
                            self.live_seq += 1
                            self.update(
                                respiration=result["rate_bpm"],
                                resp_status=result["status"].replace("_", " "),
                            )
                        self.update(
                            write=f'Saved {sum(x["chunks"] for x in self.session.manifest["streams"].values())} chunks · '
                            + time.strftime("%H:%M:%S")
                        )
                    self.update(duration=self.relative())
                    if not live:
                        self.update(
                            respiration=None,
                            resp_status="Unavailable during signal gap",
                        )
            except Exception as exc:
                self.fail_storage(exc)

    async def simulate(self):
        """Generate synthetic ECG, motion, and heart-rate packets for testing."""
        origin = time.monotonic()
        n = 0
        accn = 0
        beat = -1
        while self.want_connection:
            now = time.monotonic()
            elapsed = now - origin
            if getattr(self, "sim_gap_until", 0) > now:
                n = int(elapsed * 130)
                accn = int(elapsed * 50)
                await asyncio.sleep(0.04)
                continue
            if self.snapshot()["dropout"]:
                self.event("connection", "reconnected", simulated=True)
                self.quality("bluetooth_dropout", False)
                self.update(connection="Connected · simulator", dropout=False)
            limit = int(elapsed * 130)
            rows = []
            times = []
            for i in range(n, limit):
                t = i / 130
                # A 72 bpm beat repeats every ~0.833 s. Breathing-like modulation
                # changes its amplitude at 0.25 Hz (15 cycles per minute).
                phase = (t % 0.833333) / 0.833333
                modulation = 1 + 0.22 * math.sin(2 * math.pi * 0.25 * t)
                value = (
                    # Positive spike, negative lobe, broad recovery, and baseline.
                    1100 * modulation * math.exp(-(((phase - 0.2) / 0.018) ** 2))
                    - 220 * math.exp(-(((phase - 0.24) / 0.025) ** 2))
                    + 150 * math.exp(-(((phase - 0.5) / 0.09) ** 2))
                    + 20 * math.sin(2 * math.pi * 0.25 * t)
                )
                rows.append(
                    dict(
                        t=self.relative(origin + t),
                        uv=round(value),
                        sensor_ns=round(t * 1e9),
                        host_receipt_monotonic_s=now,
                        clock_generation=0,
                    )
                )
                times.append(origin + t)
            n = limit
            if rows:
                self.receive("ecg", rows, now, times)
            limit = int(elapsed * 50)
            rows = []
            for i in range(accn, limit):
                t = i / 50
                rows.append(
                    dict(
                        t=self.relative(origin + t),
                        x_mg=round(10 * math.sin(t)),
                        y_mg=5,
                        z_mg=1000,
                        sensor_ns=round(t * 1e9),
                        host_receipt_monotonic_s=now,
                        clock_generation=0,
                    )
                )
            accn = limit
            if rows:
                self.receive("accelerometer", rows, now)
            if int(elapsed / 0.833333) != beat:
                beat = int(elapsed / 0.833333)
                self.on_hr(
                    None, bytes([0x16, 72]) + int(1024 * 5 / 6).to_bytes(2, "little")
                )
            await asyncio.sleep(0.04)

    async def simulate_dropout(self, seconds=20):
        if not self.simulated or not self.want_connection:
            raise RuntimeError("Connect the simulator first")
        if self.snapshot()["dropout"]:
            return
        self.sim_gap_until = time.monotonic() + seconds
        self.update(
            dropout=True,
            connection="Simulator · 20s dropout",
            ready=False,
            hr=None,
            respiration=None,
        )
        self.event("connection", "disconnected", simulated=True)
        self.quality("bluetooth_dropout", True)
        if self.session:
            self.session.flush(True)

    async def stop(self):
        """Stop producers and BLE streams, then finish any healthy recording."""
        self.want_connection = False
        if self.reconnect_task:
            self.reconnect_task.cancel()
            await asyncio.gather(self.reconnect_task, return_exceptions=True)
            self.reconnect_task = None
        if self.sim_task:
            self.sim_task.cancel()
            await asyncio.gather(self.sim_task, return_exceptions=True)
            self.sim_task = None
        if self.client and self.client.is_connected:
            for kind in list(self.stream_started):
                try:
                    await self.command(bytes([3, kind]))
                except Exception as exc:
                    try:
                        self.event("connection", "stream_stop_error", message=str(exc))
                    except Exception:
                        pass
        await self.disconnect_transport()
        path = None
        if self.session:
            path = self.session.path

            # A failed writer must leave its active marker for explicit recovery.
            if not self.writer_failed:
                try:
                    if self.snapshot()["dropout"]:
                        self.event("connection", "dropout_open_at_stop")
                    self.session.finish()
                    self.update(write="Saved · session complete")
                except Exception as exc:
                    self.fail_storage(exc)
                    raise
            self.session = None
            if self.registry:
                try:
                    await asyncio.to_thread(self.registry.sync_recording, path)
                except Exception as exc:
                    self.update(error=f"Raw recording saved; registry update failed. Refresh the study registry to retry: {exc}")
        self.update(
            recording=False, ready=False, connection="Disconnected", dropout=False
        )
        return str(path) if path else None

    async def shutdown(self):
        """Finish recording before cancelling periodic housekeeping."""
        await self.stop()
        if self.house_task:
            self.house_task.cancel()
