"""Tests for tools/sweep.py, the data behind the channel notebook's mode-matching section."""

import importlib.util
import json
import pathlib

import pytest

PATH = pathlib.Path(__file__).parents[2] / "tools" / "sweep.py"
spec = importlib.util.spec_from_file_location("sweep", PATH)
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


def test_the_grid_covers_every_experiment_with_unique_points():
    points = sweep.experiments()
    keys = [sweep.key(p) for p in points]
    assert len(set(keys)) == len(keys)
    assert {p["experiment"] for p in points} == {"interleave", "rate", "delay", "doppler"}
    assert all(p["interleave"] == "zero" for p in points if p["rate"] == 4800)
    zero = {p["experiment"] for p in points if p["interleave"] == "zero" and p["rate"] != 4800}
    assert zero == {"interleave"}  # elsewhere long interleave, as Table XVI


def test_seeds_are_stable_and_distinct():
    a = sweep.seed_for("rate|2400L|2ms|1Hz|10dB", 0)
    assert a == sweep.seed_for("rate|2400L|2ms|1Hz|10dB", 0)
    others = {
        sweep.seed_for("rate|2400L|2ms|1Hz|10dB", 1),
        sweep.seed_for("rate|600L|2ms|1Hz|10dB", 0),
    }
    assert a not in others and len(others) == 2


def point(**kw):
    p = {
        "experiment": "test",
        "rate": 75,
        "interleave": "short",
        "delay_ms": 0.0,
        "doppler_hz": 0.0,
        "snr_db": 40.0,
    }
    p.update(kw)
    return p


def test_a_clean_channel_delivers_every_frame():
    p = point()
    k, c = sweep.run_message((p, sweep.key(p), 0))
    assert c["errors"] == 0 and c["frames_ok"] == c["frames"] > 0 and c["channels"] == 1


def test_a_hopeless_channel_loses_frames_and_counts_errors():
    p = point(rate=2400, interleave="short", snr_db=-10.0, delay_ms=2.0, doppler_hz=1.0)
    _, c = sweep.run_message((p, sweep.key(p), 0))
    assert c["frames_ok"] < c["frames"] and c["errors"] > 0.1 * c["bits"]


@pytest.fixture
def fake(monkeypatch):
    """Two cheap points and a fake simulation, so main() runs in an instant."""
    points = [point(experiment="delay", snr_db=1.0), point(experiment="delay", snr_db=2.0)]
    monkeypatch.setattr(sweep, "experiments", lambda: points)
    calls = []

    def run(job):
        calls.append(job[2])
        return job[1], {
            "bits": 10,
            "errors": 1,
            "frames": 2,
            "frames_ok": 1,
            "channels": 1,
            "seconds": 60.0,
        }

    monkeypatch.setattr(sweep, "run_message", run)
    return calls


def test_a_rerun_does_only_what_is_missing(tmp_path, fake):
    out = str(tmp_path / "s.json")
    sweep.main(["--experiments", "delay", "--channels", "2", "--jobs", "1", "-o", out])
    assert len(fake) == 4
    sweep.main(["--experiments", "delay", "--channels", "3", "--jobs", "1", "-o", out])
    assert len(fake) == 6 and fake[-2:] == [2, 2]  # one more message per point, numbered on
    data = json.loads(pathlib.Path(out).read_text())
    assert all(e["channels"] == 3 and e["bits"] == 30 for e in data["points"].values())


def test_merge_adds_the_counts_of_another_run(tmp_path, fake):
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    sweep.main(["--experiments", "delay", "--channels", "1", "--jobs", "1", "-o", a])
    sweep.main(["--experiments", "delay", "--channels", "2", "--jobs", "1", "-o", b])
    sweep.main(["-o", a, "--merge", b])
    data = json.loads(pathlib.Path(a).read_text())
    assert all(e["channels"] == 3 and e["frames_ok"] == 3 for e in data["points"].values())


def test_estimate_runs_nothing(tmp_path, fake):
    sweep.main(["--estimate", "--jobs", "1", "-o", str(tmp_path / "s.json")])
    assert fake == []
