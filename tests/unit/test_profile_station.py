"""tools/profile_station.py: the per-stage profile behind the performance goal (PLAN.md)."""

import cProfile
import importlib.util
import pathlib

import numpy as np

PATH = pathlib.Path(__file__).parents[2] / "tools" / "profile_station.py"
spec = importlib.util.spec_from_file_location("profile_station", PATH)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_stages_by_source_file():
    root = tool.PACKAGE
    assert tool.stage_of(f"{root}/rx/acquire.py") == "search"
    assert tool.stage_of(f"{root}/rx/equalize.py") == "demodulator"
    assert tool.stage_of(f"{root}/fec.py") == "decoder"
    assert tool.stage_of("/usr/lib/python3/numpy/core.py") is None


def test_time_in_numpy_is_charged_to_the_caller():
    """A short message: the search and the demodulator account for the time, including
    what NumPy did for them; nothing is lost in the charging."""
    x = tool.signal("600S-60B", 3.0, fading=False)
    profile = cProfile.Profile()
    profile.enable()
    detected = tool.receive(x, late_entry=False)
    profile.disable()
    totals = tool.by_stage(profile)
    assert detected is not None and detected > 0
    whole = sum(totals.values())
    assert totals["search"] + totals["demodulator"] > 0.6 * whole
    assert totals["other"] < 0.1 * whole


def test_the_short_scenario_is_a_short_message():
    x = tool.signal("600S-60B", 10.0, fading=False)
    assert x.size == 48000 * 10 and np.any(x)
