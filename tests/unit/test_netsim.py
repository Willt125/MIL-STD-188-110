"""Tests for tools/netsim.py, the many-station model of the ARQ design (docs/NETWORK.md §7):
the channel rules it is built on, and small nets whose outcome is known."""

import importlib.util
import pathlib

import numpy as np
import pytest

PATH = pathlib.Path(__file__).parents[2] / "tools" / "netsim.py"
spec = importlib.util.spec_from_file_location("netsim", PATH)
net = importlib.util.module_from_spec(spec)
spec.loader.exec_module(net)

TURNS = net.b2f_sizes()


def test_frame_success_follows_the_measured_table():
    assert net.frame_success(1200, 12.0) == pytest.approx(0.98)
    assert net.frame_success(1200, 10.5) == pytest.approx((0.77 + 0.98) / 2)
    assert net.frame_success(2400, -10.0) == 0.0 and net.frame_success(600, 30.0) == 1.0
    assert net.frame_success(1200, 6.0, 30) > net.frame_success(1200, 6.0)  # short frames


def test_the_rate_is_the_measured_arq_choice():
    assert [net.arq_rate(s) for s in (20, 10, 5, 3, 0)] == [2400, 1200, 600, 150, 75]


def matrix(*pairs, n):
    snr = np.full((n, n), -np.inf)
    for i, j, s in pairs:
        snr[i, j] = snr[j, i] = s
    return snr


def test_a_lone_session_completes_and_costs_what_one_link_costs():
    n = net.Net(matrix((0, 1, 21.0), n=2), net.ACCESS["aloha"], seed=1)
    n.at(0.0, lambda: n.offer(0, 1, TURNS))
    n.run(3600)
    (s,) = n.sessions
    assert s["ok"] and s["attempts"] == 1
    assert 25 < s["done"] < 60  # arqsim measures about 52 s with the rate still climbing


def test_interference_is_noise_and_a_transmitting_station_hears_nothing():
    snr = matrix((0, 1, 15.0), (2, 1, 15.0), (0, 2, 10.0), n=3)
    n = net.Net(snr, net.ACCESS["aloha"], seed=1)
    tx = n.transmission(0, 1, 1, 1200, [200] * 5)
    other = n.transmission(2, 1, 2, 1200, [200] * 5)
    n.txs += [tx, other]
    a, b = tx.frames[3][2:]
    assert n._sinr(tx, 1, a, b) == pytest.approx(15.0 - 10 * np.log10(1 + 10**1.5))
    assert n._transmitting(2, a, b) and not n._transmitting(1, a, b)


def test_two_callers_at_once():
    """Two clients that hear each other call one gateway at the same moment. Without a busy
    detector they collide and retry; listening first, the second waits for the first."""
    snr = matrix((0, 1, 18.0), (0, 2, 18.0), (1, 2, 12.0), n=3)
    results = {}
    for access in ("aloha", "lbt"):
        attempts = []
        for seed in range(6):
            n = net.Net(snr, net.ACCESS[access], seed=seed, gateways=1)
            n.at(0.0, lambda n=n: n.offer(1, 0, TURNS))
            n.at(0.2, lambda n=n: n.offer(2, 0, TURNS))
            n.run(4 * 3600)
            assert all(s["ok"] for s in n.sessions)
            attempts.append(sum(s["attempts"] for s in n.sessions))
            if access == "lbt":  # one after the other, never overlapping
                first, second = sorted(n.sessions, key=lambda s: s["done"])
                assert second["attempts"] == 1
        results[access] = attempts
    assert sum(results["aloha"]) > sum(results["lbt"]) == 12


def test_a_busy_station_ignores_another_caller():
    """A gateway in a session does not answer a second caller (hidden from the first), who
    times out, gives the attempt up and tries again later."""
    snr = matrix((0, 1, 18.0), (0, 2, 18.0), n=3)
    n = net.Net(snr, net.ACCESS["aloha"], seed=3, gateways=1)
    n.at(0.0, lambda: n.offer(1, 0, TURNS))
    n.at(5.0, lambda: n.offer(2, 0, TURNS))
    n.run(4 * 3600)
    first, second = n.sessions
    assert first["ok"] and second["ok"] and second["attempts"] >= 2


def test_virtual_carrier_sense_defers_after_another_sessions_head():
    snr = matrix((0, 1, 18.0), (0, 2, 18.0), (1, 2, 15.0), n=3)
    n = net.Net(snr, net.ACCESS["lbt-nav"], seed=1)
    tx = n.transmission(1, 0, 7, 1200, [200] * 3)
    n.txs.append(tx)
    n._receive(tx)
    assert n.nodes[2].nav_until == pytest.approx(tx.end + 40.0)
    n.t = tx.end + 10.0
    assert n._busy(2) and not net.Net(snr, net.ACCESS["lbt"], 1)._busy(2)


@pytest.mark.parametrize("kind", ["hub", "mesh"])
def test_topologies(kind):
    rng = np.random.default_rng(0)
    snr, callers, targets = net.topology(kind, 12, 2, 0.5, rng)
    assert np.allclose(snr, snr.T) and np.isneginf(np.diag(snr)).all()
    for c in callers:
        assert targets[c] and all(snr[c, t] >= net.ROUTINE_SNR[0] for t in targets[c])
    if kind == "hub":
        assert callers == list(range(2, 14)) and np.isfinite(snr[2:, :2]).all()
        assert np.isneginf(snr[2:, 2:][np.triu_indices(12, 1)]).any()  # hidden clients


def test_a_small_net_runs():
    args, sessions, busy, callers = net.net_job(("hub", 5, 1, 0.3, 2.0, "lbt", 1, 1.0, True))
    assert callers == 5 and sessions and 0 < busy < 1
    assert all(s["ok"] for s in sessions)
