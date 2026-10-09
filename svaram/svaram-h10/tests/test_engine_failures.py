import asyncio
import time
from pathlib import Path
import pytest
from h10rig.engine import Engine
from h10rig.protocol import PMD_CONTROL
from h10rig.storage import recover_session


@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path)
    yield e
    e.submit(e.shutdown()).result(6)
    e.loop.call_soon_threadsafe(e.loop.stop)
    e.thread.join(3)


def test_disk_failure_never_marks_complete(engine):
    e = engine
    e.submit(e.connect(simulated=True)).result(3)
    time.sleep(0.5)
    path = Path(e.submit(e.start("")).result(3))

    async def fail():
        e.session.flush = lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("disk full")
        )

    e.submit(fail()).result(3)
    time.sleep(0.4)
    assert not e.snapshot()["recording"] and "WRITE ERROR" in e.snapshot()["write"]
    assert (path / "active.json").exists()
    e.submit(e.stop()).result(3)
    recover_session(path)


def test_label_write_failure_does_not_publish_unsaved_state(engine):
    e = engine
    with pytest.raises(ValueError, match="Start recording"):
        e.submit(e.set_sound_label("fire")).result(3)
    e.submit(e.connect(simulated=True)).result(3)
    time.sleep(0.5)
    path = Path(e.submit(e.start("")).result(3))
    pressed = time.monotonic()
    e.submit(e.set_sound_label("water", pressed)).result(3)
    assert e.snapshot()["sound_label"] == "water"

    async def fail():
        e.session.set_sound_label = lambda *args: (_ for _ in ()).throw(OSError("disk full"))

    e.submit(fail()).result(3)
    with pytest.raises(OSError, match="disk full"):
        e.submit(e.set_sound_label("fire")).result(3)
    assert e.snapshot()["sound_label"] == "water"
    assert not e.snapshot()["recording"]
    assert (path / "active.json").exists()


def test_control_ack_rejection_and_multipart(engine):
    e = engine

    class Client:
        is_connected = False

        async def disconnect(self):
            pass

        async def write_gatt_char(self, char, data, response):
            assert char == PMD_CONTROL and response
            if data[0] == 2:
                e.cp_queue.put_nowait(bytes([0xF0, 2, 0, 8]))
            else:
                e.cp_queue.put_nowait(bytes([0xF0, 1, 0, 0, 1, 0, 1]))
                e.cp_queue.put_nowait(bytes([0xF0, 1, 0, 0, 0, 130, 0]))

    async def check():
        e.client = Client()
        e.cp_queue = asyncio.Queue()
        assert await e.command(bytes([1, 0])) == bytes([0, 1, 130, 0])
        with pytest.raises(RuntimeError, match="status 8"):
            await e.command(bytes([2, 0]))

    e.submit(check()).result(3)


def test_reconnect_retries_without_ending_session(engine):
    e = engine
    e.submit(e.connect(simulated=True)).result(3)
    time.sleep(0.5)
    path = e.submit(e.start("")).result(3)
    calls = []

    async def reconnect():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("temporarily unavailable")
        e.update(connection="Connected", dropout=False)

    async def check():
        e.sim_task.cancel()
        await asyncio.gather(e.sim_task, return_exceptions=True)
        e.sim_task = None
        e.simulated = False
        e.connect_real = reconnect
        e.client = object()
        e.disconnected(e.client)
        await asyncio.sleep(3.3)
        assert len(calls) == 2
        assert e.session and e.snapshot()["recording"]
        assert not e.snapshot()["dropout"]

    e.submit(check()).result(5)
    e.submit(e.stop()).result(3)
    assert "reconnected" in (Path(path) / "events/connection.jsonl").read_text()
