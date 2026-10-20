"""The buffers between devices and the station, and the virtual device (PLAN.md P1)."""

import threading

import numpy as np

from m110c.audio import InputRing, OutputQueue, VirtualDevice


def test_input_ring_delivers_blocks_in_order():
    ring = InputRing(capacity=100)
    ring.put(np.arange(5))
    ring.put(np.arange(5, 12))
    assert np.array_equal(ring.get(4), [0, 1, 2, 3])
    assert np.array_equal(ring.get(8), np.arange(4, 12))
    assert ring.get(1, timeout=0.01) is None  # nothing there
    assert ring.dropped == 0


def test_an_overrun_keeps_the_time_base():
    """Samples dropped for want of room come out as zeros: every position is delivered."""
    ring = InputRing(capacity=10)
    ring.put(np.arange(1, 9))
    ring.put(np.arange(9, 15))  # 14 held, room for 10: the 4 oldest go
    assert ring.dropped == 4
    assert np.array_equal(ring.get(14), [0, 0, 0, 0, *range(5, 15)])


def test_overruns_cost_no_memory():
    ring = InputRing(capacity=10)
    for _ in range(1000):
        ring.put(np.ones(10))
    assert ring.dropped == 9990 and ring._real == 10
    assert len(ring._pieces) <= 2  # one run of zeros, then the newest samples
    x = ring.get(10000)
    assert np.count_nonzero(x) == 10 and np.all(x[-10:] == 1)


def test_input_ring_wakes_a_waiting_reader():
    ring = InputRing(capacity=100)
    got = []
    reader = threading.Thread(target=lambda: got.append(ring.get(3, timeout=5)))
    reader.start()
    ring.put(np.ones(3))
    reader.join()
    assert np.array_equal(got[0], [1, 1, 1])
    ring.close()
    assert ring.get(1) is None


def test_output_queue_pads_with_silence():
    q = OutputQueue()
    q.put(np.ones(3, complex))
    x, missing = q.take(5)
    assert np.array_equal(x, [1, 1, 1, 0, 0]) and missing == 2 and q.size == 0


def test_silence_is_an_underrun_only_during_a_transmission():
    device = VirtualDevice(duration=9600)
    device.read()
    assert device.stats.underruns == 0  # idle: silence is normal
    device.expect_output(True)
    device.write(np.ones(100))
    device.read()
    assert device.stats.underruns == 860


def test_virtual_device_loopback_and_timeline():
    device = VirtualDevice(loopback_delay=50, duration=3 * 960)
    device.write(np.ones(10))
    blocks = [device.read() for _ in range(4)]
    assert blocks[-1] is None  # the input ended
    x = np.concatenate(blocks[:3])
    assert np.flatnonzero(x).tolist() == list(range(50, 60))
    assert np.flatnonzero(device.output).tolist() == list(range(10))
    assert device.output.size == x.size


def test_dropped_and_repeated_blocks():
    signal = np.arange(10 * 100, dtype=float)
    dropped = VirtualDevice(signal, block=100, drop_blocks={2})
    first = [dropped.read() for _ in range(3)]
    assert first[2][0] == 300  # block 2 lost: block 3 arrives in its place
    repeated = VirtualDevice(signal, block=100, repeat_blocks={2})
    first = [repeated.read() for _ in range(4)]
    assert first[2][0] == 100 and first[3][0] == 200
    gapped = VirtualDevice(signal, block=100, gap_blocks={2})
    first = [gapped.read() for _ in range(4)]
    assert not np.any(first[2]) and first[3][0] == 300 and gapped.stats.overruns == 100
