import json
import zlib

from h10rig.documents import extract_ids, load_document


def test_csv_header_row_and_first_data_row():
    data = b"participant_id,session_id,facilitator_code,notes\nPT-025,S-2026-019,FC-03,ok\n"
    assert extract_ids("answers.csv", data) == {
        "p": "PT-025",
        "s": "S-2026-019",
        "f": "FC-03",
    }


def test_csv_key_value_rows():
    data = b"field,value\nParticipant ID,PT-1001\nSession number,S-12-3\n"
    assert extract_ids("q.csv", data) == {"p": "PT-1001", "s": "S-12-3"}


def test_survey_export_layout_reads_the_answer_column():
    # The baseline form's CSV export: Section, Field, Question, Answer (UTF-8 BOM).
    data = (
        '﻿"Section","Field","Question","Answer"\n'
        '"About you","participant_id","Participant code (required)","002"\n'
        '"About you","session_id","Session code (required)","01"\n'
        '"About you","age_years","Age in years","23"\n'
    ).encode("utf-8")
    assert extract_ids("baseline.csv", data) == {"p": "002", "s": "01"}


def test_header_row_is_not_read_as_a_key_value_pair():
    data = b"participant_id,session_id\n,S-1-2\n"
    assert extract_ids("q.csv", data) == {"s": "S-1-2"}


def test_nested_json_keys():
    doc = {
        "meta": {"participant": {"participant_id": "PT-77"}},
        "rows": [{"session_id": "S-9-9"}],
    }
    assert extract_ids("q.json", json.dumps(doc).encode()) == {
        "p": "PT-77",
        "s": "S-9-9",
    }


def test_json_null_values_are_ignored():
    assert extract_ids("q.json", b'{"participant_id": null}') == {}


def test_file_name_with_underscores_and_dots():
    assert extract_ids("consent_PT-025_S-2026-019.FC-03.jpg", b"") == {
        "p": "PT-025",
        "s": "S-2026-019",
        "f": "FC-03",
    }


def test_flate_pdf_with_split_tj_fragments():
    content = b"BT /F1 12 Tf [(Participant: PT-)-20(025)] TJ ET"
    stream = zlib.compress(content)
    pdf = (
        b"%%PDF-1.4\n1 0 obj << /Length %d /Filter /FlateDecode >>\nstream\r\n"
        % len(stream)
        + stream
        + b"\r\nendstream\nendobj\n%%EOF"
    )
    assert extract_ids("consent.pdf", pdf) == {"p": "PT-025"}


def test_unreadable_content_still_scans_the_name():
    assert extract_ids("PT-031.json", b"{not json") == {"p": "PT-031"}


def test_load_document_hashes_and_keeps_bytes(tmp_path):
    f = tmp_path / "consent.pdf"
    f.write_bytes(b"abc")
    d = load_document(f)
    assert d.size == 3 and d.data == b"abc"
    assert (
        d.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert d.meta()["type"] == "application/pdf"
