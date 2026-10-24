# Benches and on-air acceptance

The modem program (PLAN.md §6, P1-P8) is tested in CI through fakes and the virtual air.
What CI cannot do is described here: real sound cards, real clocks, real SDRs, and, later,
real radios. Each bench says what it tests and what it needs. It also gives the setup,
what to run, and what to record. Results go in the log at the end of each bench, with the
date, the hardware and the commit.

Written 2026-10-20, revised 2026-10-24 for the equipment at hand and the shopping list at
the end:
- spare laptops (one to three);
- a LimeSDR (USB, v1);
- an RTL-SDR (v1);
- a Behringer UMC22 (USB audio interface).

The Baofeng UV-5R is not used in this project: an FM handheld with filtered, emphasised
audio represents nothing an HF data station uses.

**Representative, not expensive.** The benches use the parts real digital-mode stations
use, which are mostly ordinary ones: a C-Media CM108-family codec in a USB interface (the
Digirig), or the 16-bit USB codec built into a radio. Better converters would make a test
less representative, not more. What the benches avoid is the anonymous part: a dongle
whose chip is unknown and which may apply gain control or noise suppression nobody asked
for. The UMC22, cleaner than either, is kept as a *reference*: what works on it and fails
on a Digirig points at the interface; what fails on both points at the modem.

There is no licence yet. Benches A-E and G need none. Bench F needs a licence and an HF
transceiver, and waits for both.

---

## Rules for every bench

These are US rules as we understand them. This is not legal advice; check your own
jurisdiction.

- **Nothing that can transmit is ever connected to an antenna without a licence that
  covers it.** That includes the LimeSDR's transmit ports. On benches A-E,
  transmit paths are cables: audio between computers, coax and attenuators between SDRs.
- **Receiving needs no licence.** Recording band noise and other stations (bench E) is
  fine. Recordings of other people's transmissions are kept as test data, not published.
- **Never cable a transmitter straight into a receiver.** Put fixed attenuators between
  them (bench D), and start every session at the lowest transmit gain.
- **The modem's amateur profile refuses to transmit without a callsign.** On benches A-D and G,
  where nothing reaches the air, use an obviously fictitious one (`N0CALL`, as the tests
  do). Never use someone else's callsign.

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
- the audio interfaces' own behaviour, representative (Digirig) and reference (UMC22);
- Pat to Pat over a real audio path;
- Winlink Express, if one laptop runs Windows.

**Needs:**
- two laptops;
- two Digirig Mobile interfaces, one per laptop, and a cable joining their audio jacks
  (each one's transmit audio to the other's receive audio: wire it from Digirig's
  published pinout, or use their cables and a small adapter);
- for the reference run, the UMC22 on one laptop instead of its Digirig: a cable from a
  UMC22 main output (quarter-inch TRS) to the Digirig's receive audio, and from the
  Digirig's transmit audio to UMC22 input 1 (quarter-inch, the combo jack's line input).

Laptops' own headset jacks are avoided: they are combined (TRRS) jacks with microphone
processing in the way, and represent nothing a station uses.

**Setup:**

```
laptop 1 ── Digirig 1 ── TX audio ──> RX audio ── Digirig 2 ── laptop 2
                         RX audio <── TX audio
reference: Digirig 1 replaced by the UMC22 (main out -> Digirig RX, Digirig TX -> input 1)
```

- **Levels:** set in the operating system's mixer (and, on the UMC22, its gain and output
  knobs) so that the modem's meter (`--levels 2`) shows peaks of about -10 to -6 dBFS and
  no clipping. A Digirig's transmit audio is meant for a radio's microphone input and its
  receive side for a radio's speaker output, so expect to raise the receiving side's gain.
- **UMC22:** Direct Monitor *off* (it mixes the input into the outputs: the station would
  hear and repeat itself), phantom power off.
- Turn off anything the operating system does to an input: automatic gain, noise
  suppression, echo cancellation, "enhancements". Each of them damages 8-PSK; check again
  after updates.

**Run:**
- every mode, both directions, `m110c-modem` on each side, e.g.
  `m110c-modem --audio --callsign N0CALL --chat --rate 1200` on both laptops, typing on
  one and reading on the other (`--list-audio` names the cards; `-s 48000`);
- the same with the UMC22 at one end;
- an hour of continuous reception: memory, dropped buffers, clock offset over time;
- a 10-minute message;
- then sessions (P5);
- Pat to Pat (P6).

**Record:**
- the clock offset each receiver reports, which is the two interfaces' difference, and
  its drift;
- dropped and repeated buffers;
- errors per mode, Digirig to Digirig and with the UMC22;
- the levels used.

| Date | Laptops, OS | Interfaces | Commit | Clock offset (ppm) | Result |
|---|---|---|---|---|---|
| | | | | | |

## Bench C: one interface looped back, and a keying line (P1, P3)

**Tests:**
- each interface on its own, its output cabled back to its input;
- serial keying, and its timing against the audio;
- `rigctld` keying through Hamlib's dummy rig;
- the transmit watchdog;
- unplugging a device mid-transmission (the keying line must drop).

**Needs:**
- the UMC22 with a quarter-inch TRS cable from a main output to input 1;
- a Digirig with a cable from its transmit audio to its own receive audio;
- for watching the keying line: the Digirig's PTT output switching an LED (an LED, a
  resistor of about 1 kΩ and a small 5 V source, such as a USB breakout, wired as
  Digirig's documentation shows for its PTT output). The Digirig's keying comes from its
  serial port's RTS line, so no separate USB-serial adapter is needed;
- Hamlib, for `rigctld -m 1` (its dummy rig).

**Run:**
- `M110C_AUDIO_LOOPBACK=<card> pytest tests/acceptance/test_benches.py -s` with each
  interface in turn (UMC22: Direct Monitor off, gain set as in bench B): every mode out
  and back, full duplex;
- each keying method (P3);
- the watchdog: hold a transmission past its limit and check the line drops;
- pull the USB cable mid-transmission;
- to time keying against the audio, record the LED with the laptop's camera, or feed the
  keying line through a divider into the UMC22's second input as a step beside the audio.

**Record:**
- SNR and errors per mode, per interface;
- the delay from key to audio, and from audio end to unkey, per method;
- whether the watchdog and the unplug test leave the line unkeyed.

Real radio latency waits for bench F.

| Date | Interface | Commit | Method | Key to audio (ms) | Result |
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
- the LimeSDR (transmitter) and two receivers in turn: the RTL-SDR v1 (its crystal is
  often tens of ppm off: the hard case for the receiver's corrections) and an RTL-SDR Blog
  V4 (a 1-ppm oscillator: the easy case, and HF without modification);
- short SMA cables (RG316 assemblies);
- fixed SMA attenuators whose values can be trusted: Mini-Circuits VAT-10+, VAT-20+ and
  VAT-30+ (at least 40 dB in line). Generic ones work for a loop, but their real
  attenuation and match are unknown, and the levels here are measurements;
- 50 Ω SMA terminations for the unused ports, and optionally an SMA DC block (Mini-Circuits
  makes both);
- SoapySDR with its LimeSDR and RTL-SDR modules (the V4 needs the RTL-SDR Blog driver).

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
- an HF receiver with room for strong signals: an Airspy HF+ Discovery (its strong-signal
  handling on HF suits crowded bands; SoapySDR has a driver for it). Until then, the
  LimeSDR (weak below a few MHz, usable on 7-30 MHz) or the RTL-SDR Blog V4;
- an antenna: a random wire with a 9:1 unun, or an inexpensive active loop. A few metres
  of wire is enough to start.

**Setup:**
- the antenna on a **receive** port only;
- terminate the transmit ports, and keep the modem's transmitter off (receive-only SDR
  device).

**Run:** recordings of 10-60 minutes each (the SDR device's receive side, P1, or the
receiver's own tools), as I/Q or audio with the frequency, time and
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
- **An HF transceiver, a linear (SSB) one.** Two good choices:
  - *a used Icom IC-7300*: the closest thing to a reference radio among Winlink, VARA and
    ARDOP users, with built-in USB audio and CAT control on one cable. If the modem works
    on it, it works where most users are;
  - *a Yaesu FT-891 with one of the Digirigs*: cheaper, and with no built-in USB audio it
    tests the external-interface path most older radios use.

  Buying used or borrowing is fine. *Avoid* single-tone digital-mode designs (QDX, QMX
  and the like) and polar-modulation rigs such as the (tr)uSDX: built for FT8-style single
  tones, they cannot transmit 8-PSK faithfully. Be wary of radios with a doubtful record
  for transmit purity (some inexpensive SDR transceivers) for the spectrum measurement.
  A SignaLink (VOX, isolated) is worth adding only to test VOX keying (P3).
- **A dummy load chosen by its continuous rating**, not its headline wattage: many "100 W"
  loads are rated 20 W continuous. Off-the-shelf HF loads that combine 100 W continuous
  with a sampling port are hard to find; the practical arrangement is a load rated well
  above the test power, with a separate inline RF sampler. Run 110, a continuous signal,
  at 10-25 W for long tests.
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

## Bench G: three stations on a shared audio "ether" (P4, P5; collisions)

**Tests:** what happens between more than two real stations, with real transmissions
rather than a model of them:
- collisions: two waveforms from interfaces on independent clocks, overlapping at a
  receiver;
- capture: how much stronger one signal must be for the receiver to keep it through the
  other (tools/netsim.py assumes a receiver keeps the first signal it locked onto);
- listen before talk and the busy detector (P4) against real signals and real latency;
- hidden stations (G2): two stations that cannot hear each other, both reaching a third;
- later, sessions (P5) with a third station interfering, and a few of netsim's
  measurements (NETWORK.md §7) repeated on hardware.

No radio and no RF: it works because the stations are half duplex. Every station's output
goes into a sum, and the sum goes into every station's input. A station hears its own
signal only while transmitting, when it is deaf anyway (the station mutes its input), so it
does not matter that it is in the sum.

What it cannot show: fading (each path would need its own processing; that is P2's
virtual air, and the hybrid in PLAN.md P2), a radio's automatic gain control (a strong
signal makes a real receiver turn down, which changes who wins a collision), and anything
of real propagation. For linear (SSB) radios, summing at audio is otherwise equivalent to
summing at RF.

**Needs:**
- three computers: the two laptops and the main computer;
- three interfaces: the two Digirigs and the UMC22 (the reference card is useful here: it
  makes one station's signal easy to tell apart from the interfaces' own effects);
- and one of:
  - **G1, one bus (everyone hears everyone):** a small analog mixer with at least three
    line inputs and a main output, with the main output split three ways (a passive
    splitter, or the mixer's main and monitor outputs). Its faders set each station's
    strength. Set its EQ flat and switch off any effects or compressor;
  - **G2, a matrix (hidden stations too):** a small passive box, below.
- quarter-inch and 3.5 mm cables to suit each interface;
- audio isolation transformers (one per path to the main computer), only if hum appears.

**G2, the matrix box.** One summing node per receiving station, fed by the two *other*
stations, each through its own path: a pot that sets the path's loss, and a switch that
breaks it. The two 10 kΩ resistors into a node are the mixer; the node goes straight to
that station's receive input, with no further mixing (a final mixer would make every
station hear the same sum again: that is G1). No self path: it is not needed, and leaving
it out lets a station run full duplex for tests without hearing itself.

```
       station 1's RX input     station 2's RX input     station 3's RX input
         (jack R1)                (jack R2)                (jack R3)
            ^                        ^                        ^
          node 1                   node 2                   node 3
        (1 k to gnd)             (1 k to gnd)             (1 k to gnd)
          ^      ^                 ^      ^                 ^      ^
        10 k   10 k              10 k   10 k              10 k   10 k     summing resistors
          |      |                 |      |                 |      |
        S21    S31               S12    S32               S13    S23      path switches (SPST)
          |      |                 |      |                 |      |
        P21    P31               P12    P32               P13    P23      10 k audio-taper pots,
          |      |                 |      |                 |      |      wired as dividers to gnd
          T2     T3                T1     T3                T1     T2

Tj: station j's TX output (jack Tj), feeding its two pots. Pjk and Sjk: the path from
station j to station k. All grounds common.
```

Each station plugs into the box twice:

```
station 1 (laptop + interface)
   TX audio out ──> jack T1 ──> P12, S12 (to station 2) and P13, S13 (to station 3)
   RX audio in  <── jack R1 <── node 1: P21, S21 (from station 2) + P31, S31 (from station 3)
```

**A pot alone cannot hide a pair; the switch can.** Fully down, a pot still passes its end
resistance's share: a few ohms to tens of ohms of 10 kΩ, so the path is only about
46-70 dB below fully up. With clean converters that may still be well above the receiving
interface's noise floor (for example, a path at -10 dBFS turned 46 dB down arrives at
-56 dBFS, decodable over a floor near -90 dBFS; the receiver finds preambles at about -9 dB
SNR). An open switch breaks the path; what remains is stray coupling through wiring and
the shared ground, expected 80 dB or more down. Pots set the levels; switches make pairs
hidden. (A pull-down at the wiper would not help: the wiper never floats, and a resistor
there would only load the pot and bend its taper.)

Parts: six 10 kΩ audio-taper (logarithmic) potentiometers, six small SPST toggle switches,
six 10 kΩ and three 1 kΩ resistors, three input and three output jacks to suit the cables,
a small metal box, hookup wire: about $25-40. Passive summing loses level (on the order of
20 dB with these values), which the receiving interfaces' gain makes up; the UMC22's line
output is much hotter than a Digirig's, so its pots run lower (or add a pad on its two
paths). Build it on a breadboard first and check the levels before boxing it up.

**Setup:**
- Run the laptops on battery. If hum appears with the main computer on mains power, put
  isolation transformers in its paths.
- UMC22: Direct Monitor off, phantom power off (as in bench B).
- **Calibrate the paths:** each station in turn sends a message (`m110c-modem --audio
  --callsign N0CALL --send ...`); the other two note the level (`--levels 1`) and the
  SNR reported. Write down the matrix of levels in dB; set the pots (or faders) for the
  test, and record the settings.
- **Each receiver's noise floor:** with nothing sent, note each station's input level. A
  path is hidden only if what leaks through it lies well below this (the hiding margin).
- Noise, if wanted: G1, a fourth mixer channel playing noise from any source; G2 runs on
  the interfaces' own noise floor, so the pots set signal-to-interference rather than SNR.

**Run:**
1. *Collisions by hand* (from P1 on, no busy detector needed): stations 1 and 2 send
   600S messages to station 3 at about the same moment (`--send`, started together by hand:
   the overlap matters, not its exact timing), at relative levels of 0, 6, 10 and 20 dB. Note which message, if
   either, station 3 decodes, and how many bytes are damaged.
2. *Late arrivals:* station 2 starts while station 1's message is under way, weaker and
   stronger. Does station 3 keep station 1 (netsim's "capture" rule), switch, or lose
   both?
3. *Listen before talk* (P4): stations that hear each other defer; count the collisions
   left against netsim's figures.
4. *Hidden stations* (G2): first check the pair is really hidden: open the switches between
   stations 1 and 2 (S12 and S21), send from each, and confirm the other reports nothing
   (no preamble found, no change on `--levels`); note any residual. Then, with both still
   reaching station 3, run listen before talk: it no longer protects them; compare with
   netsim.
5. *Sessions with an interferer* (P5): a session between stations 1 and 3 while station 2
   sends at random.

**Record:**
- the level matrix, the pot or fader settings and which switches are open;
- each receiver's noise floor, and the residual level of each opened path;
- for each collision: relative level, overlap, what was decoded, bytes damaged;
- the level difference at which the stronger signal survives (capture), for comparison
  with netsim's assumption;
- busy-detector decisions and collisions avoided or not (P4 on).

| Date | G1 or G2 | Stations, interfaces | Commit | Test | Relative level (dB) | Result |
|---|---|---|---|---|---|---|
| | | | | | | |

---

## What to acquire, in order

Prices are rough, from listings of varying age (2026-10); check before buying.

| When | For | Item | Rough cost |
|---|---|---|---|
| Now | B, C | two Digirig Mobile interfaces (digirig.net; cables sold separately) and their cables | $130-150 |
| Now | B, C | shielded quarter-inch TRS cables (pro-audio brand, e.g. Hosa) for the UMC22 (already owned) | $15-25 |
| Now | C | an LED, a resistor, a small 5 V source (USB breakout) for watching the keying line | $5-10 |
| Now | D | Mini-Circuits VAT-10+, VAT-20+, VAT-30+; RG316 SMA cables; 50 Ω SMA terminations; optionally a DC block | $60-90 |
| Now | D, E | an RTL-SDR Blog V4 (keep the v1 too) | about $40 |
| Next | E | an Airspy HF+ Discovery, and a receive antenna (random wire and 9:1 unun, or an active loop) | $170-250 |
| Next | G | G1: a small analog mixer (3+ line inputs) and a 1-to-3 splitter; or G2: matrix-box parts (six 10 kΩ audio-taper pots, six SPST toggles, resistors, jacks, a box); audio isolation transformers only if hum appears | $20-60 |
| Later | F | licence exams | inexpensive |
| Later | F | a used IC-7300, or an FT-891 (with a Digirig already owned) | the one large cost |
| Later | F | a dummy load rated continuous above the test power; an inline RF sampler and attenuators; a power and SWR meter | $100-250 |

Not needed: the UV-5R, a cheap anonymous USB sound card, a separate USB-serial adapter
(the Digirig has one), a SignaLink before P3, a hardware channel simulator (the software
one does that job), a signal generator or an oscilloscope (the SDRs cover the timing
measurements).

Benches A-E and G cover P1, P2, P4, P5 and P6, and P3 except real radio timing. Only bench F's
measurements and the final on-air acceptance wait for the licence.
