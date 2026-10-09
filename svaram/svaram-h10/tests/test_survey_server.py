import importlib.util
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

import pytest

spec = importlib.util.spec_from_file_location("survey_server", Path(__file__).resolve().parents[2] / "svaram-data/server.py")
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


@pytest.fixture
def survey(tmp_path):
    server = server_module.SurveyServer(("127.0.0.1", 0), tmp_path / "data")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def request(server, path, data=None, token=True):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Study-Token"] = server.token
    req = Request(f"http://127.0.0.1:{server.server_port}" + path,
                  json.dumps(data).encode() if data is not None else None, headers)
    with urlopen(req, timeout=3) as response:
        return json.load(response)


def test_http_survey_round_trip_and_conflict(survey):
    pid = request(survey, "/api/participants", {"code": "P-001"})["id"]
    sid = request(survey, "/api/sessions", {"participant_id": pid})["id"]
    body = {"session_id": sid, "kind": "pre", "data": {"answers": {"state_calm": 0, "state_stress": None}, "answer_times": {}}, "version": 0}
    result = request(survey, "/api/assessment", body)
    body.update(version=result["version"], submit=True, request_id=str(uuid.uuid4()))
    submitted = request(survey, "/api/assessment", body)
    assert request(survey, "/api/assessment", body) == submitted
    latest = request(survey, f"/api/assessment?session={sid}&kind=pre")
    assert latest["data"]["answers"]["state_calm"] == 0
    assert latest["session"]["status"] == "ready"
    body.update(submit=False, version=0)
    with pytest.raises(HTTPError) as exc:
        request(survey, "/api/assessment", body)
    assert exc.value.code == 409


def test_missing_token_and_bad_rating_rejected(survey):
    with pytest.raises(HTTPError) as exc:
        request(survey, "/api/participants", {"code": "P-001"}, token=False)
    assert exc.value.code == 403
    pid = request(survey, "/api/participants", {"code": "P-001"})["id"]
    sid = request(survey, "/api/sessions", {"participant_id": pid})["id"]
    with pytest.raises(HTTPError) as exc:
        request(survey, "/api/assessment", {"session_id": sid, "kind": "pre", "version": 0,
            "data": {"answers": {"state_calm": 11}, "answer_times": {}}})
    assert exc.value.code == 400
    assert not survey.registry.assessment(sid, "pre")["submitted"]


def test_pages_reuse_baseline_and_remove_automatic_browser_restore(survey):
    for path in ("/", "/baseline", "/post"):
        with urlopen(f"http://127.0.0.1:{survey.server_port}{path}") as response:
            html = response.read().decode()
            assert 'name="api-token"' in html
            assert response.headers["Cache-Control"] == "no-store"
            assert "restore();conditional();progress();" not in html
    assert len(survey.post["states"]) == 8
