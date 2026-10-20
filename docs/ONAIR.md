# Benches and on-air acceptance

The modem program (PLAN.md §6, P1-P8) is tested in CI through fakes and the virtual air.
What CI cannot do is described here: real sound cards, real clocks, real SDRs, and, later,
real radios. Each bench says what it tests and what it needs. It also gives the setup,
what to run, and what to record. Results go in the log at the end of each bench, with the
date, the hardware and the commit.

Written 2026-10-20 for the equipment at hand:
- spare laptops (one to three);
- a LimeSDR (USB, v1);
- an RTL-SDR (v1);
- a Baofeng UV-5R.

There is no licence yet. Benches A-E need none. Bench F needs a licence and an HF
transceiver, and waits for both.

---

## Rules for every bench

These are US rules as we understand them. This is not legal advice; check your own
jurisdiction.

- **Nothing that can transmit is ever connected to an antenna without a licence that
  covers it.** That includes the LimeSDR's transmit ports and the UV-5R. On benches A-E,
  transmit paths are cables: audio between laptops, coax and attenuators between SDRs.
- **Receiving needs no licence.** Recording band noise and other stations (bench E) is
  fine. Recordings of other people's transmissions are kept as test data, not published.
- **Never cable a transmitter straight into a receiver.** Put fixed attenuators between
  them (bench D), and start every session at the lowest transmit gain.
- **The modem's amateur profile refuses to transmit without a callsign.** On benches A-D,
  where nothing reaches the air, use an obviously fictitious one (`N0CALL`, as the tests
  do). Never use someone else's callsign.
- **The UV-5R stays receive-only.** Transmitting needs a licence. Even then it is a poor
  test transmitter: FM only, emphasised and filtered audio, notoriously poor spurious
  emissions. Never key it without an antenna or dummy load, or it can damage itself.

---

## Bench A: one laptop (P1, P2)

**Tests:** the real-time core on a real operating system, and the virtual air (P2) with
several modem processes. Most of P5 and P6 is tested the same way, before any other bench.

**Needs:** one laptop. Linux is proposed (the CI platform).

**Run:**
- the test suite;
- `m110c-air` with two to four `m110c-modem` processes, sessions on the moderate and
  harsh channels;
- (P6) two Pat instances over the virtual air, `M110C_PAT` set.

**Record:**
- processing time per second of signal for each mode, against PERFORMANCE.md's M12
  figures;
- missed real-time deadlines (P1 counts them);
- session times, against arqsim's (NETWORK.md §7).

| Date | Laptop, OS | Commit | Result |
|---|---|---|---|
| | | | |

## Bench B: two laptops and an audio cable (P1, P6)

**Tests:**
- real converters;
- two independent clocks (the transmit and receive clock offsets the receiver must
  follow);
- the audio input's own processing;
- Pat to Pat over a real audio path;
- Winlink Express, if one laptop runs Windows.

**Needs:**
- two laptops;
- a 3.5 mm cable (about $5);
- TRRS splitters, if the laptops have one combined headset jack (about $5 each);
- a 20-40 dB attenuator pad from headphone out to mic in. Mic inputs expect tens of
  millivolts and clip on a headphone level. Inline pads exist. Two resistors also do,
  for example 10 kΩ in series and 100 Ω to ground, about 40 dB.

**Setup:**

```
laptop 1 headphone out ──[pad 20-40 dB]──> laptop 2 mic in
laptop 2 headphone out ──[pad 20-40 dB]──> laptop 1 mic in     (second cable, for sessions)
```

- Turn off everything the operating system does to the mic input: automatic gain, noise
  suppression, echo cancellation, "enhancements". Each of them damages 8-PSK, so check
  them again after updates.
- Set the input level so that the modem's level meter (P1) shows no clipping, with
  margin.

**Run:**
- every mode, both directions, `m110c-modem` on each side, e.g.
  `m110c-modem --audio --callsign N0CALL --chat --rate 1200` on both laptops, typing on
  one and reading on the other (`--list-audio` names the cards; `--levels 2` shows the
  input level while you set it);
- an hour of continuous reception: memory, dropped buffers, clock offset over time;
- a 10-minute message;
- then sessions (P5);
- Pat to Pat (P6).

**Record:**
- the clock offset each receiver reports, which is the two sound cards' difference, and
  its drift;
- dropped and repeated buffers;
- errors per mode;
- the input level used.

| Date | Laptops, OS | Commit | Clock offset (ppm) | Result |
|---|---|---|---|---|
| | | | | |

## Bench C: USB sound cards and a keying line (P1, P3)

**Tests:**
- the USB sound cards a station would really use;
- serial and GPIO keying, and their timing against the audio;
- `rigctld` keying through Hamlib's dummy rig;
- the transmit watchdog;
- unplugging a device mid-transmission (the keying line must drop).

**Needs:**
- two CM108-based USB sound cards (about $10 each). The CM108's GPIO pin is a keying
  method in its own right, and some cards bring it out;
- a USB-serial adapter, FTDI or CP2102 (about $10);
- an LED and a resistor (about 1 kΩ) on its RTS line, as a stand-in for the radio's PTT;
- Hamlib, for `rigctld -m 1` (its dummy rig).

**Setup:** the cards replace the built-in jacks of bench B. A pad is still needed if a
card's mic input is used.

**Run:**
- with one card's output cabled to its own input (through the pad),
  `M110C_AUDIO_LOOPBACK=<card> pytest tests/acceptance/test_benches.py -s`: every mode out
  and back, full duplex;
- each keying method (P3);
- the watchdog: hold a transmission past its limit and check the line drops;
- pull the USB cable mid-transmission;
- to time keying against the audio, record the LED with the laptop's camera, or wire RTS
  through a divider into the second card's input as a step.

**Record:**
- the delay from key to audio, and from audio end to unkey, per method;
- whether the watchdog and the unplug test leave the line unkeyed.

Real radio latency waits for bench F.

| Date | Card, adapter | Commit | Method | Key to audio (ms) | Result |
|---|---|---|---|---|---|
| | | | | | |

## Bench D: SDR loop over coax (P1's SDR device)

**Tests:** the whole radio path with real hardware and no air:
- the SDR device's up- and down-conversion;
- real oscillators: the RTL-SDR's crystal is often tens of ppm off, which tests the
  receiver's carrier and clock correction harder than any sound card;
- the converters' noise and spurs;
- gain staging.

**Needs:**
- the LimeSDR (transmitter) and the RTL-SDR (receiver);
- two SMA cables;
- fixed SMA attenuators: 10, 20 and 30 dB (at least 40 dB in line);
- a 50 Ω terminator for each unused port;
- optionally a DC block;
- SoapySDR with its LimeSDR and RTL-SDR modules.

**Setup:**

```
LimeSDR TX ──[30 dB]──[10-20 dB]──> RTL-SDR RX          unused LimeSDR ports: 50 Ω
```

- **Frequency:** use one inside the RTL-SDR's range. A plain v1 dongle has no HF without
  a modification; over coax the frequency makes no difference to the test. In the US,
  pick one in the 902-928 MHz ISM band (915 MHz, for example). Nothing is meant to
  radiate, but whatever leaks then lands where unlicensed devices already operate.
- **Levels:**
  - start at the LimeSDR's lowest transmit gain, with all the attenuation in line;
  - aim for about -50 to -30 dBm at the RTL-SDR, well clear of its overload (around
    0 dBm) and of its damage level (around +10 dBm);
  - the LimeSDR's maximum output is around +10 dBm, so 40 dB of attenuation keeps even a
    mistake safe.
- **The modem's bench cap:** the amateur profile's SDR cap (PLAN.md P1) keeps the
  LimeSDR's output low on this bench. Do not lift it here.

**A variation with a shared clock:** the LimeSDR into its own receive port, through the
same attenuators, at an HF frequency. This tests the LimeSDR's HF path. The two ends
share one reference clock, so there is no clock offset to follow.

**Run:**
- `M110C_SDR_LOOP="driver=lime;driver=rtlsdr" M110C_SDR_DIAL=915e6 pytest
  tests/acceptance/test_benches.py -s` (the LimeSDR at 960 kS/s, the RTL-SDR at 240 kS/s,
  both at the bench level); or by hand, `m110c-modem --sdr driver=rtlsdr --dial 915e6
  --sdr-tx off` listening while `m110c-modem --sdr driver=lime --sdr-rate 960000 --dial
  915e6 --callsign N0CALL --chat` sends;
- every mode;
- a sweep of transmit gain to find where the signal is clean and where it starts to
  distort;
- long messages, for the RTL-SDR's drift as it warms up.

**Record:**
- frequency and clock offsets measured;
- the receiver's SNR against the expected level (transmit power less attenuation, over
  the RTL-SDR's noise floor);
- the gain at which 2400 bps first loses data (distortion);
- spurs seen in the receiver's spectrum.

| Date | Devices | Frequency | Attenuation | Commit | Offsets (Hz, ppm) | Result |
|---|---|---|---|---|---|---|
| | | | | | | |

## Bench E: listening (P4)

**Tests:** the busy detector against the real bands: noise, other modes, voice, fades,
and the false alarms in hours of noise. The synthetic stand-ins of P4 are checked against
these recordings.

**Needs:**
- the LimeSDR (receive only) and a wire antenna for HF: a few metres of wire is enough
  to hear the bands. The LimeSDR USB is weak below a few MHz but usable on 7-30 MHz;
- optionally the UV-5R's audio into a laptop, for VHF/UHF data and voice heard through an
  FM radio.

**Setup:**
- the antenna on a **receive** port only;
- terminate the transmit ports, and keep the modem's transmitter off (receive-only SDR
  device).

**Run:** recordings of 10-60 minutes each (the SDR device's receive side, P1, or the
LimeSDR's own tools), as I/Q or audio with the frequency, time and
band conditions noted. Wanted:
- quiet bands at night and by day;
- crowded segments: FT8 (for example 14.074 MHz), RTTY contests, PSK31;
- SSB voice;
- any 110 or STANAG signals heard (the late-entry search will flag them);
- local noise (switching supplies).

**Record:**
- the busy detector's decisions against what is audible or visible on a waterfall;
- false alarms per hour on quiet bands.

The recordings stay out of the repository: other people's transmissions. They are run as
`M110C_RECORDINGS`-style opt-in tests.

| Date | Band, frequency | Duration | Contents | Busy detector result |
|---|---|---|---|---|
| | | | | |

## Bench F: on the air (P3, P6; needs a licence and an HF transceiver)

**Tests:** what only a real transmitter and the air can show:
- the transmitted spectrum (D5: within 2.8 kHz at -26 dB with roll-off 0.15);
- keying latency and turnaround of real radios (replacing arqsim's assumed 0.5 s);
- Pat to Pat between two stations;
- Winlink Express;
- the carrier question (1800 or 1500 Hz, NETWORK.md §5).

**Needs:**
- **A licence.** In the US, Technician permits digital modes on all bands above 50 MHz,
  and on HF only 28.0-28.3 MHz (10 m). General opens most HF data segments. The two exams
  can be taken in one sitting.
- **An HF transceiver with a built-in USB sound card**, which covers audio, CAT keying
  and usually RTS keying with one cable. Buying used or borrowing is fine.
  - *Avoid* the single-tone digital-mode designs (QDX, QMX and the like) and
    polar-modulation rigs such as the (tr)uSDX. They are built for FT8-style single
    tones and cannot transmit 8-PSK faithfully.
  - An interface such as a Digirig (serial keying) or a SignaLink (VOX, isolated) serves
    a rig without one, and tests those keying methods.
- **A dummy load** rated for continuous duty at the transmitter's full power. Run 110,
  a continuous signal, at 25-50% of the rig's rating for long tests.
- **An inline power and SWR meter.**
- **An RF sampler** (or a dummy load with a sampling port) **and fixed attenuators**,
  bringing the signal to around -30 dBm for the SDRs:
  - 100 W is +50 dBm, so about 80 dB in total;
  - only the first stage handles the full power;
  - the LimeSDR, or an RTL-SDR with HF, measures the spectrum through it.
- **For the D5 check:** keep the transmitter's ALC at or near zero. Overdrive widens the
  spectrum more than the pulse shape narrows it.

**Run, in order:**
1. Into the dummy load:
   - the spectrum at roll-off 0.15, across drive levels;
   - keying latency per method, with `tools/turnaround.py` (P3) and an SDR on the
     sampler.
2. Over the air, at low power, with a cooperating licensed station:
   - every mode;
   - sessions;
   - Pat to Pat (P6);
   - Winlink Express.

**Record:**
- the -26 dB and 99% bandwidths per drive level;
- latency and turnaround per radio and method;
- session results, with both stations' reports.

| Date | Radio, interface | Commit | Test | Result |
|---|---|---|---|---|
| | | | | |

---

## What to acquire, in order

| For | Item | Rough cost |
|---|---|---|
| B | 3.5 mm cables, TRRS splitters, a 20-40 dB audio pad (or two resistors) | $10-20 |
| C | two CM108 USB sound cards; a USB-serial adapter; an LED and resistor | $25-35 |
| D | SMA attenuators (10, 20, 30 dB), SMA cables, 50 Ω terminators, optionally a DC block | $20-40 |
| E | wire for an HF receive antenna | a few dollars |
| F | licence exams; an HF transceiver with USB audio; a dummy load; a power meter; an RF sampler and attenuators | the licence is inexpensive; the rest is the one large cost, and can wait |

Benches A-E cover P1, P2, P4, P5 and P6, and P3 except real radio timing. Only bench F's
measurements and the final on-air acceptance wait for the licence.
