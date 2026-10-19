# The network stack: goals, decisions, open questions

Record of the network-stack discussion (2026-10-19) that PLAN.md §6 scheduled before the
modem program. It replaces that agenda as the place where the stack's direction is kept.
Milestones come after the open questions in §6 below are settled.

Status labels as in PLAN.md: **[DECIDED]**, **[PROPOSED]**, **[OPEN]**.

---

## 1. What 110 + STANAG 5066 solve, in the abstract

MIL-STD-188-110 alone is a one-way bit pipe with awkward properties:

- **Half-duplex, with a high fixed cost per turn.** Every transmission pays a preamble
  (0.6 s, or 4.8 s with long interleave), radio keying and an interleaver flush; the
  toystack measured at least 1.2 s per transmission (PLAN.md S8).
- **Bursty residual errors.** FEC and interleaving clean up most errors, but a fade still
  removes a run of bits.
- **A channel that changes**, so the best data rate changes from minute to minute.
- **No addressing, no framing beyond the EOM, no recovery.**
- **A shared medium**: everyone on the frequency hears everything and can collide.

STANAG 5066's job, abstractly: *turn a scarce, slow, lossy, half-duplex, shared broadcast
channel into a shared message-delivery service.* Its goals, separately:

1. **Pay the turnaround cost rarely**: many small frames per transmission, with
   acknowledgements carried in the reply. This is the main efficiency idea.
2. **Lose small pieces, not whole messages**: segments of a few hundred bytes with their
   own CRCs; selective-repeat ARQ with a window as large as a transmission.
3. **Match the rate to the channel**: the receiver reports conditions, the sender changes
   the 110 mode.
4. **Decide who talks when**: channel access, and links held open for a session.
5. **Reach stations that cannot or will not answer**: broadcast without ARQ, repeated.
6. **Let urgent traffic jump the queue**: priorities and expedited data.
7. **Let several applications share one radio**: client access points, with delivery
   confirmation at a chosen level.

All of it assumes a managed network: trained operators, planned frequencies, encryption,
a chain of command.

## 2. The civilian and emergency setting

The channel is the same; the surroundings are not.

- **Rules.** Amateur service in the first place (§4, D1). In the US (Part 97, to be
  checked in each jurisdiction): no encryption or obscured meaning, publicly documented
  techniques, periodic identification, and limits on automatic control.
- **Topology.** Mostly hub and spoke (an emergency operations centre and the field, or a
  net control station and its check-ins); stations come and go; nobody administers the
  network; operators range from expert to first-timer; propagation windows open and close.
- **Traffic.** Forms and messages (ICS-213, situation reports, rosters: mostly 0.5-3 kB,
  compressible), bulletins and weather (broadcast), short status and check-ins, operator
  chat, the odd small file or image, and e-mail gateways (Winlink is the incumbent).
  Precedence in the manner of ARRL's (emergency, priority, welfare, routine).
- **Courtesy and law.** Listen before transmitting (the frequency is shared with people
  outside the net), identify, keep transmissions reasonably short.

## 3. What we need, goal by goal

| 5066 goal | Civilian equivalent | Verdict |
|---|---|---|
| Pay turnaround rarely | many CRC-checked frames per transmission; acks in the reply | **Keep** (the core) |
| Lose small pieces | selective-repeat ARQ, window = one transmission | **Keep** |
| Match rate | mode chosen from SNR reports carried in acks (toystack: the report reads 3-4 dB low, consistently, ±1.5 dB per message) | **Simplify**: a field, not a protocol |
| Who talks when | listen before transmit; within a session the sender holds the turn | **Simplify** |
| Reach the silent | repeated (or erasure-coded) broadcast for bulletins | **Keep** |
| Priority | four precedence levels on the send queue, applied at the next transmission | **Simplify** |
| Many applications | a few clients behind one host interface, one session at a time | **Simplify** |
| Addressing | callsigns, which also identify the station as the rules require | **Replace** |
| Encryption, emission control, hard-link management, 5066's management layer | not allowed (amateur) or not needed yet | **Drop** |
| Frequency selection (ALE) | manual; a frequency-availability client later | **Defer** |

PLAN.md's decision stands: no STANAG 5066 on the air, now or later. 110C §4.4 says an ARQ
protocol used with it "shall be in accordance with STANAG 5066"; ours is outside 110C, and
the documentation will say so.

## 4. Decisions

**D1 [DECIDED] Amateur first, legal by default.** The obvious way to use the software is
the legal way:

- the callsign is the station's address and is sent in every transmission; nothing is
  transmitted without one configured;
- no encryption, now or later;
- every protocol of ours is specified in this repository (a requirement for amateur use,
  e.g. US 97.309);
- no automatic answering by default: a station that responds to connection requests
  unattended is under automatic control, which on HF the US allows only in certain band
  segments (97.221). The software will say clearly when it is acting as an automatic
  station;
- the README gets an "On the air" section: a licence is needed; meeting the rules of
  your jurisdiction is the operator's responsibility, not the authors'; what the software
  does to help; and the MIT licence's warranty disclaimer.

Writing, publishing and running the code off the air needs no licence; on-air use is the
station licensee's responsibility (in the US, 97.103). 110C is "Distribution Statement A:
Approved for public release; distribution unlimited". SHARES and MARS users operate under
their own authorisations; the stack does not preclude them and has nothing specific to them.

**D2 [DECIDED] Winlink interoperability is a goal.** Three levels, in rising dependence on
others:

1. B2F, Winlink's message protocol (publicly documented; open implementations such as Pat),
   between two stations running this modem, peer to peer. Entirely ours to do.
2. A host interface compatible with ARDOP's, so that Winlink clients that drive ARDOP
   (Pat, Winlink Express) use a 110 link unchanged. Both ends still need this modem. See §5.
3. Gateways (RMS) accepting 110 connections: possible in principle, since gateway software
   drives ARDOP the same way, but up to gateway operators and Winlink's policies. Enabled,
   not promised.

**D3 [PROPOSED] Stack shape.** B2F is the message layer, so we do not invent a mail
format. Below it, what ARDOP and VARA offer, built our way:

- *frames*: many CRC-checked frames per 110 transmission, with the callsign;
- *ARQ sessions*: connect and disconnect between two callsigns, a reliable byte stream,
  selective repeat, acks with an SNR report, mode changes;
- *broadcast (FEC)*: no ARQ, repetitions, for bulletins and listeners;
- *clients*: B2F first, operator chat, later file transfer and the others on PLAN.md's list.

One session at a time, as amateur stations work; not 5066's several clients over one link.

**D4 [PROPOSED] Compatible host interface, not "ARDOP".** The ARDOP specification
(Rev 0.3.1, §3.0) reserves "ARDOP compatible or compliant" for implementations that pass its
conformance tests, which concern ARDOP's own waveform. Ours is a different waveform: we
emulate the host interface only, and the documentation says "a host interface compatible
with ARDOP's", never "ARDOP".

## 5. The ARDOP specification, checked (Rev 0.3.1, 25 March 2015)

What the specification covers: ARDOP's waveform and frames, its ARQ and FEC protocol rules,
and its state machine. What it does not: the host interface. Appendix E is a placeholder
that points to "a separate detailed Host interface document". So the question "does the
host interface fit 110?" is **[OPEN]** until we have that document; the practical reference
is what Pat's ARDOP driver (open source) actually sends and expects.

What the specification does tell us, and how 110 maps onto it:

| ARDOP | What it means for us |
|---|---|
| ARQ between two callsigns (CONREQ/CONACK; ISS sends, IRS receives; roles swap with IDLE and BREAK; DISC/END; a session timeout) | The same session model D3 needs. Our state machine can mirror its states (DISC, ISS, IRS, ISS-to-IRS, FEC send, FEC receive), which the host interface reports |
| One data frame per transmission, ACK/NAK with a 5-bit decode quality, even/odd toggling against duplicates | Stop-and-wait. Fine for ARDOP's short leaders; for 110, with 0.6 s or 4.8 s per preamble, we keep many frames per transmission (D3). The host does not see the difference |
| Bandwidths of 200, 500, 1000 and 2000 Hz, forced or negotiated (ARQBW) | 110 has one bandwidth, about 3 kHz (below). Hosts that ask for 2000 Hz or less: either refuse, or accept and say so in the documentation **[OPEN]** |
| ID frame at least every 10 minutes and at the end of a session, with optional CW ID | Matches D1; we need the same (an ID frame in our own framing, which keeps every transmission identified) |
| Busy detector, listen before transmit, connection refused when busy (CONREJBSY) | We need a busy detector. The late-entry search (S5) detects 110 signals, but not other modes; a general energy-and-occupancy detector is also needed |
| Automatic leader timing from measured round trips (Fig. D-1); VOX delays up to 500 ms | We need the same: keying latency differs between radios and interfaces. Part of the audio and keying milestone |
| Session ID: an 8-bit hash of both callsigns, guarding against a neighbouring session's frames | Worth borrowing in our frame header |
| FEC broadcast: 0-5 repeats, receivers pass only error-free frames, others flagged as containing errors | The broadcast mode of D3; the flag "containing errors" is a good host-interface idea |

### The bandwidth fact

Measured on our modulator (root-raised cosine, roll-off 0.25):

| Roll-off | 99% power (110C's "occupied bandwidth") | -26 dB (Part 97's definition) |
|---:|---:|---:|
| 0.25 (ours) | 2653 Hz | 2995 Hz |
| 0.15 | 2505 Hz | 2780 Hz |
| 0.10 | 2452 Hz | 2754 Hz |

110C requires an occupied bandwidth (99% of the power) of at most 3 kHz; ours is 2.65 kHz.
Whether US amateur rules now impose a 2.8 kHz limit on HF data is **[OPEN]**. My recollection
is that the FCC removed the symbol-rate limit and only proposed the 2.8 kHz limit, but this
must be checked. If the limit applies, a roll-off of 0.15 meets it at the -26 dB points; a
receiver filtering with 0.25 then loses a little, and interoperability with other 110
modems needs checking (PLAN.md S9). An earlier note of mine, "110 fits", was wrong as
stated: at roll-off 0.25 it does not fit 2.8 kHz.

## 6. Open questions and facts still needed

- **The ARDOP host-interface document**, or Pat's ARDOP driver read as the reference: which
  commands and responses real clients use, and whether any assume ARDOP's bandwidths or
  frame types.
- **The current US rule on HF data bandwidth** (2.8 kHz or not), and the -26 dB point
  as measured on a real transmitter (our numbers are at audio).
- **Keying latency and turnaround** on real radios (PTT, VOX, CAT), for leader timing
  and timeouts: the audio and keying milestone.
- **Fade lengths** on the channels that matter (NVIS; 2 paths at 2 ms and 1 Hz; 5 ms and
  5 Hz), to size frames: measurable with the channel simulator now.
- **A cap on transmission length**, for fairness and courtesy.
- **Message sizes** of real emergency traffic, to size the windows and compression.
- **B2F's specification**: which revision, its compression (Winlink's documented variant
  of LZHUF), and how Pat implements it.
- **Gateway policy**: whether RMS operators could accept a new transport, and on what
  terms. Not needed before level 2 of D2 works.
- **Busy detection** of signals other than 110.

## 7. Next steps

1. Get the host-interface definition (the ARDOP host interface document, and Pat's driver)
   and check it against D3 and D4.
2. Settle the bandwidth question; if 2.8 kHz applies, measure what roll-off 0.15 costs our
   receiver and others'.
3. Prototype frames and selective-repeat ARQ sessions in `tools/toystack.py` over the
   channel simulator, with B2F-sized traffic, before writing milestones.
4. Then write the modem program's milestones in PLAN.md: audio and keying, the host
   interface, the session layer, B2F, the TUI.
