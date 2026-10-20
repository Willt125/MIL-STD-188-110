"""Operating profiles (m110c.profile; docs/NETWORK.md, D7): amateur by default, with its
limits enforced; authorized lifts them and always says so."""

import numpy as np
import pytest

from m110c import profile
from m110c.cli import tx_main
from m110c.modulate import DEFAULT_ROLLOFF, Modulator


def test_amateur_is_the_default_and_allows_the_default_rolloff():
    assert profile.DEFAULT is profile.AMATEUR
    assert profile.check_rolloff(profile.AMATEUR, None) == DEFAULT_ROLLOFF == 0.15
    assert profile.check_rolloff(profile.AMATEUR, 0.1) == 0.1


@pytest.mark.parametrize("rolloff", [0.2, 0.25, 0.35])
def test_amateur_refuses_wider_rolloffs_and_says_what_lifts_the_limit(rolloff):
    with pytest.raises(ValueError, match="--profile authorized"):
        profile.check_rolloff(profile.AMATEUR, rolloff)
    assert profile.check_rolloff(profile.AUTHORIZED, rolloff) == rolloff


def test_unknown_profile():
    with pytest.raises(ValueError, match="amateur, authorized"):
        profile.get("military")


def test_the_notice_says_the_safeties_are_off_and_whose_job_it_is():
    notice = profile.AUTHORIZED_NOTICE
    assert "ALL SAFETIES ARE OFF" in notice
    assert "YOU must make sure" in notice and "licence or authorisation" in notice
    assert "2.8 kHz" in notice and "without" in notice  # the limit lifted; no warranty


def test_the_default_transmit_filter_is_long_and_narrow():
    m = Modulator()
    assert m.taps.size == 2 * 16 * m.sps + 1


def tx(tmp_path, *args):
    src = tmp_path / "in.txt"
    src.write_bytes(b"profile test")
    out = tmp_path / "out.cf32"
    return tx_main([str(src), "-o", str(out), "-r", "2400", *args]), out


def test_tx_amateur_refuses_a_wide_rolloff(tmp_path, capsys):
    code, out = tx(tmp_path, "--rolloff", "0.25")
    err = capsys.readouterr().err
    assert code == 2 and "amateur profile allows" in err and "--profile authorized" in err
    assert not out.exists() or out.stat().st_size == 0


def test_tx_authorized_transmits_and_prints_the_notice_even_when_quiet(tmp_path, capsys):
    code, out = tx(tmp_path, "--profile", "authorized", "--rolloff", "0.35", "-q")
    err = capsys.readouterr().err
    assert code == 0 and "ALL SAFETIES ARE OFF" in err
    x = np.fromfile(out, np.complex64)
    assert x.size > 0


def test_tx_amateur_says_nothing_extra(tmp_path, capsys):
    code, _ = tx(tmp_path, "-q")
    assert code == 0 and capsys.readouterr().err == ""
