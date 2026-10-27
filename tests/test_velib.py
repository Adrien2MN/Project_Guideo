import json
from dataclasses import replace
from pathlib import Path

from guideo.velib.client import parse_status
from guideo.velib.storage import VelibStore

FIXTURE = Path(__file__).parent / "fixtures" / "station_status.json"


def load():
    return parse_status(json.loads(FIXTURE.read_text()))


def test_parse_splits_mechanical_and_ebike():
    updated, stations = load()
    assert updated == 1761497400
    s = stations[0]
    assert (s.mechanical, s.ebike, s.docks) == (3, 1, 31)
    assert s.is_renting


def test_only_changes_are_stored(tmp_path):
    _, stations = load()
    store = VelibStore(tmp_path / "v.sqlite")

    assert store.save_poll(100, None, stations) == 2      # first poll: everything is new
    assert store.save_poll(160, None, stations) == 0      # nothing changed

    moved = [replace(stations[0], mechanical=2, docks=32), stations[1]]
    assert store.save_poll(220, None, moved) == 1         # one bike taken

    n_polls = store.conn.execute("SELECT COUNT(*) FROM polls").fetchone()[0]
    assert n_polls == 3
    store.close()


def test_last_state_survives_restart(tmp_path):
    _, stations = load()
    path = tmp_path / "v.sqlite"
    store = VelibStore(path)
    store.save_poll(100, None, stations)
    store.close()

    reopened = VelibStore(path)                            # e.g. after a PC reboot
    assert reopened.save_poll(160, None, stations) == 0
    reopened.close()
