"""The walkthrough (docs/walkthrough.py, and the notebook built from it) must keep running
against the current code, and must end with the message received intact."""

import pathlib
import runpy

import pytest


def test_walkthrough_runs_and_receives_the_message(capsys, monkeypatch):
    pytest.importorskip("matplotlib")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    import matplotlib

    matplotlib.use("Agg")
    script = pathlib.Path(__file__).resolve().parents[2] / "docs" / "walkthrough.py"
    runpy.run_path(str(script), run_name="__main__")
    out = capsys.readouterr().out
    assert "EOM found: True" in out and "identical to the message: True" in out
