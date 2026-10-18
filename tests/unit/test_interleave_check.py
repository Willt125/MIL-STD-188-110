"""Tests for tools/interleave_check.py, the measurements behind S4's decision rule."""

import importlib.util
import pathlib

PATH = pathlib.Path(__file__).parents[2] / "tools" / "interleave_check.py"
spec = importlib.util.spec_from_file_location("interleave_check", PATH)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

from m110c.rx.autointerleave import DECIDE_MARGIN  # noqa: E402


def test_a_margin_measurement_on_a_clean_channel():
    """Noise only, 18 dB: decodable, and the true hypothesis is well ahead in every block."""
    _, out = check.margins_job((1200, "zero", 1, 18.0, 7))
    assert out["decodable"] and len(out["margins"]) == check.BLOCKS
    assert min(out["margins"]) > DECIDE_MARGIN


def test_an_end_to_end_measurement():
    """2400 bps, the noise-only Table XVI row, 3 dB above its SNR: auto finds zero and
    receives exactly what a receiver told the truth does."""
    _, out = check.end_to_end_job((2400, "zero", 2, 3, 1000))
    assert out["mode"] == out["expected"] == "2400Z"
    assert out["auto"] == out["truth"] == 0.0


def test_the_reports(capsys):
    check.report_margins([((600, "short", 2, 9, 7), {"margins": [0.1, 0.12], "decodable": True})])
    check.report_end_to_end(
        [((600, "short", 7, 0, 1), {"auto": 0.0, "truth": 0.0, "mode": "600S", "expected": "600S"})]
    )
    out = capsys.readouterr().out
    assert "  600  1      |    1 +0.1000" in out
    assert "1 messages (1 decodable when told the truth): 0 received in the wrong mode" in out
