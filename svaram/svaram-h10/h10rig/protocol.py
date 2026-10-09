"""Polar PMD and Bluetooth SIG Heart Rate Measurement decoding.
See README protocol references. Unsupported packets fail visibly, never fabricate data.
"""

# Polar's proprietary measurement service and standard Bluetooth characteristics.
PMD_SERVICE = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CONTROL = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"
HR = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"


class ProtocolError(ValueError):
    """A packet is truncated or uses a format this decoder does not support."""

    pass


def signed(b):
    """Decode a little-endian signed integer, including 24-bit ECG samples."""
    return int.from_bytes(b, "little", signed=True)


def decode_pmd(data):
    """Return (stream, last_sample_ns, rows) from an ECG or motion packet."""
    if len(data) < 10:
        raise ProtocolError("Truncated PMD header")
    # PMD header: measurement ID, 8-byte final-sample timestamp, frame type.
    kind = data[0]
    timestamp_ns = int.from_bytes(data[1:9], "little")
    frame = data[9]
    payload = data[10:]

    # ECG frame 0 stores signed 24-bit amplitudes in microvolts.
    if kind == 0 and frame == 0:
        if not payload or len(payload) % 3:
            raise ProtocolError("Truncated ECG sample")
        return (
            "ecg",
            timestamp_ns,
            [{"uv": signed(payload[i : i + 3])} for i in range(0, len(payload), 3)],
        )
    if kind == 2 and frame in (0, 1, 2):
        # Motion frames 0/1/2 use 1/2/3 bytes per axis, in X/Y/Z order.
        axis_bytes = frame + 1
        if not payload or len(payload) % (axis_bytes * 3):
            raise ProtocolError("Truncated accelerometer sample")
        rows = []
        for sample_offset in range(0, len(payload), axis_bytes * 3):
            axes = {}
            for axis_index, axis_name in enumerate(("x_mg", "y_mg", "z_mg")):
                axis_offset = sample_offset + axis_index * axis_bytes
                axes[axis_name] = signed(
                    payload[axis_offset : axis_offset + axis_bytes]
                )
            rows.append(axes)
        return "accelerometer", timestamp_ns, rows
    raise ProtocolError(
        f"Unsupported PMD measurement {kind}, frame 0x{frame:02x}; original packet retained"
    )


def decode_hr(data):
    """Decode heart rate, optional contact/energy fields, and RR ticks.

    RR intervals stay in their original units: 1 tick = 1/1024 second.
    """
    if len(data) < 2:
        raise ProtocolError("Truncated HR measurement")
    flags = data[0]
    offset = 1
    # Flag bit 0 chooses an 8-bit or 16-bit heart-rate field.
    width = 2 if flags & 1 else 1
    if len(data) < offset + width:
        raise ProtocolError("Truncated HR value")
    heart_rate = int.from_bytes(data[offset : offset + width], "little")
    offset += width

    # Bit 3 adds energy expenditure; bit 4 adds trailing 16-bit RR intervals.
    energy_kj = None
    if flags & 8:
        if len(data) < offset + 2:
            raise ProtocolError("Truncated energy field")
        energy_kj = int.from_bytes(data[offset : offset + 2], "little")
        offset += 2
    rr_ticks = []
    if flags & 16:
        if (len(data) - offset) % 2:
            raise ProtocolError("Truncated RR interval")
        rr_ticks = [
            int.from_bytes(data[i : i + 2], "little")
            for i in range(offset, len(data), 2)
        ]
    # Bit 2 says contact detection is supported; bit 1 gives its current state.
    contact = bool(flags & 2) if flags & 4 else None
    return dict(
        bpm=heart_rate,
        rr_ticks=rr_ticks,
        contact=contact,
        flags=flags,
        energy_kj=energy_kj,
    )


def parse_settings(data):
    """Read the sensor's setting IDs and their lists of supported values."""
    setting_widths = {
        0: 2,
        1: 2,
        2: 2,
        3: 4,
        4: 1,
        5: 4,
        6: 16,
        7: 1,
        8: 1,
        9: 2,
        10: 4,
        11: 4,
        12: 1,
    }
    settings = {}
    offset = 0
    # Each block contains a setting ID, a value count, then fixed-width values.
    while offset < len(data):
        if offset + 2 > len(data):
            raise ProtocolError("Truncated settings header")
        kind, count = data[offset : offset + 2]
        offset += 2
        if kind not in setting_widths:
            raise ProtocolError(f"Unknown setting {kind}")
        width = setting_widths[kind]
        if offset + count * width > len(data):
            raise ProtocolError("Truncated settings values")
        settings[kind] = [
            int.from_bytes(data[value_offset : value_offset + width], "little")
            for value_offset in range(offset, offset + count * width, width)
        ]
        offset += count * width
    return settings


def start_command(kind, available):
    """Build a PMD start request only if the required settings are offered."""
    # Setting IDs: 0 = sample rate (Hz), 1 = resolution (bits), 2 = range (g).
    wanted = {0: 130, 1: 14} if kind == 0 else {0: 50, 1: 16, 2: 8}
    for setting, value in wanted.items():
        if value not in available.get(setting, []):
            raise ProtocolError(
                f"H10 does not offer required setting {setting}={value}; available: {available}"
            )
    # Opcode 2 starts a stream; each selected setting carries exactly one value.
    return bytes([2, kind]) + b"".join(
        bytes([k, 1]) + v.to_bytes(2, "little") for k, v in wanted.items()
    )


class SensorClock:
    """ One anchor shared by PMD streams; integer sensor deltas avoid precision loss."""

    def __init__(self):
        self.anchor_sensor = None
        self.anchor_host = None
        self.last = {}
        self.generation = 0

    def reset(self):
        """Discard the shared anchor after a reconnect or sensor clock reset."""
        self.anchor_sensor = None
        self.anchor_host = None
        self.last = {}
        self.generation += 1

    def map(self, stream, stamp, count, hz, receipt):
        """Reconstruct sample times from the packet's final sensor timestamp.

        Return host monotonic seconds, integer sensor nanoseconds, and timing
        events. ECG and motion share an anchor so their timelines align.
        """
        events = []
        previous = self.last.get(stream)
        if previous is not None and stamp <= previous:
            events.append(
                ("sensor_clock_reset", {"previous_ns": previous, "sensor_ns": stamp})
            )
            self.reset()
            previous = None
        if self.anchor_sensor is None:
            self.anchor_sensor = stamp
            self.anchor_host = receipt
            events.append(
                (
                    "clock_anchor",
                    {
                        "sensor_ns": stamp,
                        "host_monotonic_s": receipt,
                        "generation": self.generation,
                    },
                )
            )
        sample_period_ns = 1e9 / hz
        if previous is not None:
            observed_period_ns = (stamp - previous) / count
            # Follow small clock drift, but retain the nominal spacing across gaps.
            if abs(observed_period_ns - sample_period_ns) < sample_period_ns * 0.03:
                sample_period_ns = observed_period_ns
            else:
                events.append(
                    (
                        (
                            "missing_samples"
                            if observed_period_ns > sample_period_ns
                            else "abnormal_data_rate"
                        ),
                        {
                            "signal": stream,
                            "expected_span_s": count / hz,
                            "actual_span_s": (stamp - previous) / 1e9,
                        },
                    )
                )
        # Work backwards from the final sample. Subtract integer sensor timestamps
        # before converting to seconds to avoid rounding away nanosecond detail.
        sensor_times = [
            stamp - round((count - 1 - i) * sample_period_ns) for i in range(count)
        ]
        host_times = [
            self.anchor_host + (s - self.anchor_sensor) / 1e9 for s in sensor_times
        ]
        self.last[stream] = stamp
        return host_times, sensor_times, events
