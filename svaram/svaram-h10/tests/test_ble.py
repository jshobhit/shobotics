from h10rig.ble import ECG_START, decode_ecg, parse_hr


def rr_bytes(*ms):
    """RR values in ms -> little-endian 1/1024 s ticks."""
    return b"".join(round(v * 1024 / 1000).to_bytes(2, "little") for v in ms)


def test_hr_uint8_with_rr_and_contact():
    # flags: RR present (0x10), contact supported (0x04) and detected (0x02)
    contact, rr = parse_hr(bytes([0x16, 72]) + rr_bytes(800, 812))
    assert contact is True
    assert rr == [800, 812]


def test_hr_without_rr():
    contact, rr = parse_hr(bytes([0x04, 64]))  # contact supported, not detected
    assert contact is False
    assert rr == []


def test_hr_uint16_value_and_energy_are_skipped():
    # flags: uint16 HR (0x01), energy (0x08), RR (0x10); no contact support
    data = bytes([0x19]) + (72).to_bytes(2, "little") + (500).to_bytes(2, "little")
    contact, rr = parse_hr(data + rr_bytes(1000))
    assert contact is None
    assert rr == [1000]


def test_rr_ticks_convert_to_ms():
    _, rr = parse_hr(bytes([0x10, 60]) + (1024).to_bytes(2, "little"))
    assert rr == [1000]


def test_ecg_frame_decodes_signed_24_bit_samples():
    header = bytes([0x00]) + bytes(8) + bytes([0x00])
    samples = [0, 1, -1, 1234, -8388608, 8388607]
    payload = b"".join(v.to_bytes(3, "little", signed=True) for v in samples)
    assert decode_ecg(header + payload) == samples


def test_non_ecg_frame_is_ignored():
    assert decode_ecg(bytes([0x02]) + bytes(9) + bytes(6)) == []


def test_ecg_start_command():
    # ECG, 130 Hz (0x0082), 14-bit (0x000E)
    assert ECG_START.hex() == "02000001820001010e00"
