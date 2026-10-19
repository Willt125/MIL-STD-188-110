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

**D4 [DECIDED] Compatible host interface, not "ARDOP".** The ARDOP specification
(Rev 0.3.1, §3.0) reserves "ARDOP compatible or compliant" for implementations that pass its
conformance tests, which concern ARDOP's own waveform. Ours is a different waveform: we
emulate the host interface only, and the documentation says "a host interface compatible
with ARDOP's", never "ARDOP". Section 5 checks the interface; it fits.

**D5 [PROPOSED] A narrower pulse for amateur use.** Part 97's 2.8 kHz limit on HF data
(confirmed by the user, 2026-10-19), measured at -26 dB, is exceeded by our roll-off of 0.25
(3.0 kHz). The transmitter's roll-off becomes a setting, with 0.15 (2.78 kHz at audio) as
the amateur default; 0.25 stays available where the rules allow it. The margin at 0.15 is
thin (20 Hz at audio, before a real transmitter's distortion), so the on-air spectrum must
be measured before this is settled (§6). Other 110 modems recommend wider pulses still
(Appendices C and D of 110C suggest a roll-off of 0.35), so a third-party 110 modem may not
be usable on US amateur HF as it stands.

## 5. ARDOP, checked against 110

Two documents: the ARDOP specification (Rev 0.3.1, 25 March 2015: waveform, frames,
protocol rules, state machine) and the Host Interface Spec for the ARDOP Protocol (Rev 0.2,
15 October 2017, Muething and Wiseman). Neither is copied here; both are summarised only
as far as the fit with 110 needs.

### The protocol (specification Rev 0.3.1)

How ARDOP's protocol maps onto 110:

| ARDOP | What it means for us |
|---|---|
| ARQ between two callsigns (CONREQ/CONACK; ISS sends, IRS receives; roles swap with IDLE and BREAK; DISC/END; a session timeout) | The same session model D3 needs. Our state machine can mirror its states (DISC, ISS, IRS, ISS-to-IRS, FEC send, FEC receive), which the host interface reports |
| One data frame per transmission, ACK/NAK with a 5-bit decode quality, even/odd toggling against duplicates | Stop-and-wait. Fine for ARDOP's short leaders; for 110, with 0.6 s or 4.8 s per preamble, we keep many frames per transmission (D3). The host does not see the difference |
| Bandwidths of 200, 500, 1000 and 2000 Hz, forced or negotiated (ARQBW) | 110 has one bandwidth, about 2.8 kHz (below), wider than ARDOP's widest. See the host interface's bandwidth row |
| ID frame at least every 10 minutes and at the end of a session, with optional CW ID | Matches D1; we need the same (an ID frame in our own framing, which keeps every transmission identified) |
| Busy detector, listen before transmit, connection refused when busy (CONREJBSY) | We need a busy detector. The late-entry search (S5) detects 110 signals, but not other modes; a general energy-and-occupancy detector is also needed |
| Automatic leader timing from measured round trips (Fig. D-1); VOX delays up to 500 ms | We need the same: keying latency differs between radios and interfaces. Part of the audio and keying milestone |
| Session ID: an 8-bit hash of both callsigns, guarding against a neighbouring session's frames | Worth borrowing in our frame header |
| FEC broadcast: 0-5 repeats, receivers pass only error-free frames, others flagged as containing errors | The broadcast mode of D3; the flag "containing errors" is a good host-interface idea |

### The host interface (Rev 0.2, 2017)

The host talks to the modem ("TNC") over TCP, on two consecutive ports (8515 and 8516 by
default): text commands and asynchronous replies on the first, data on the second. A serial
form (WA8DED or SCS CRC host mode, and a PACTOR emulation) also exists; TCP is what Pat and
Winlink Express use, so it comes first and the serial forms are out of scope for now.

**Verdict: it fits.** The interface describes a session (connect to a callsign, send and
receive bytes, disconnect), a broadcast mode and the radio around them. It has no notion of
ARDOP's frames or of stop-and-wait, so 110 with many frames per transmission sits behind it
unchanged. The command groups and what each needs from us:

| Commands and replies | Fit for 110 |
|---|---|
| Identity and setup: `MYCALL`, `MYAUX`, `GRIDSQUARE`, `INITIALIZE`, `VERSION`, `CLOSE`, `CODEC`, sound devices, `DRIVELEVEL` | Direct |
| Sessions: `ARQCALL <call> <repeats>`, `LISTEN`, `PROTOCOLMODE ARQ\|FEC`, `DISCONNECT`, `ABORT`, `ARQTIMEOUT`; replies `NEWSTATE`, `PENDING`, `CANCELPENDING`, `TARGET`, `CONNECTED <call> <bw>`, `DISCONNECTED`, `STATUS`, `FAULT` | Direct: the session layer of D3 must have these states (`DISC`, `ISS`, `IRS`, `IRStoISS`, `IDLE`, `QUIET`, `FECSend`, `FECRcv`, `OFFLINE`). `LISTEN` defaults to true in ARDOP; ours defaults to false (D1) |
| Turn-taking: `AUTOBREAK`, `BREAK` | Direct: who holds the turn in a half-duplex session |
| Queues: `BUFFER` (bytes queued, including sent but unacknowledged), `DATATOSEND`, `PURGEBUFFER`, `RESTOREBUFFER` | Direct; with selective repeat, "unacknowledged" is per frame |
| Channel: `BUSYDET`, `BUSYBLOCK`, `BUSY TRUE\|FALSE`, `REJECTEDBUSY`, `SQUELCH`, `TUNERANGE` | Needs our busy detector (§6). `TUNERANGE` (up to ±200 Hz) maps to the acquisition's offset range (±400 Hz) |
| Identification: `SENDID`, `CWID`, `FECID`; data tagged `IDF` | Direct (D1) |
| Keying: `PTT TRUE\|FALSE` (the host must key within 50 ms), `LEADER`, `TRAILER` | `LEADER` has no 110 meaning (the preamble is fixed); it becomes the keying lead time. Turnaround timing is ours to measure (§6) |
| Probing: `PING <call> <repeats>`, `ENABLEPINGACK`; replies `PING`, `PINGACK`, `PINGREPLY` with S/N (in 3 kHz) and a decode quality | Direct, and useful: the S/N report is what our mode choice needs. Ours reads 3-4 dB low until calibrated (§3) |
| Broadcast: `FECSEND`, `FECREPEATS <0-5>`, `FECMODE <ARDOP mode name>`; data tagged `FEC` or `ERR` | `FECMODE`'s values are ARDOP's waveforms. We need our own names (e.g. a 110 rate and interleave) and a decision on ARDOP names sent by hosts that only know those **[OPEN]** |
| Bandwidth: `ARQBW <200\|500\|1000\|2000>MAX\|FORCED`; `REJECTEDBW`; the bandwidth in `CONNECTED` | The one real mismatch: 110 has one bandwidth, about 2.8 kHz (D5), wider than ARDOP's widest setting. Settings of 200-1000 Hz must be refused (`FAULT`, or `REJECTEDBW` on a connection), never ignored: hosts use them to keep within band plans. But refusing 2000 too would refuse every host. Proposed: accept `2000MAX` and `2000FORCED` as "the widest the host can ask for", document that 110 then occupies 2.8 kHz, and report the true bandwidth in `CONNECTED` if the hosts' parsers allow a value outside ARDOP's list, else 2000 with the deviation documented **[OPEN]** |
| Radio control: `RADIO*` (frequency, PTT port, filters, models) | Optional in ARDOP too; out of scope at first (a `FAULT`). Hamlib could serve it later |
| Data port: host to modem `D:` + 2-byte length + bytes; modem to host `d:` + length + `ARQ`, `FEC`, `ERR` or `IDF` + bytes | Direct |

Two cautions. First, the document is "Rev 0.2" and partly provisional; deployed modems and
hosts may differ from it in small ways (for example whether the `C:` and `D:` prefixes are
sent over TCP). The reference for what works is therefore what Pat's ARDOP driver (open
source) sends and parses, read and tested against before we claim compatibility. Second,
Winlink Express is closed: compatibility with it can only be tested, not read.

### The bandwidth fact

Measured on our modulator (root-raised cosine, roll-off 0.25):

| Roll-off | 99% power (110C's "occupied bandwidth") | -26 dB (Part 97's definition) |
|---:|---:|---:|
| 0.25 (ours) | 2653 Hz | 2995 Hz |
| 0.15 | 2505 Hz | 2780 Hz |
| 0.10 | 2452 Hz | 2754 Hz |

110C requires an occupied bandwidth (99% of the power) of at most 3 kHz; ours is 2.65 kHz.
US amateur rules limit HF data to 2.8 kHz (confirmed by the user), so roll-off 0.25 does not
fit; an earlier note of mine, "110 fits", was wrong. Roll-off 0.15 does (D5).

What a narrower transmit pulse costs our receiver (which keeps its 0.25 filter), a first
look: six 20-s messages per point, SNR in 3 kHz.

| Mode, channel, SNR | TX 0.25 | TX 0.15 | TX 0.10 |
|---|---:|---:|---:|
| 2400S, noise only, 7 dB | 2.0e-4 | 2.4e-4 | 2.6e-4 |
| 2400S, 2 paths 2 ms 1 Hz, 15 dB | 2.9e-3 | 5.4e-3 | 6.5e-3 |
| 600S, noise only, -1 dB | 1.4e-3 | 1.4e-3 | 1.5e-3 |
| 600S, 2 paths 2 ms 1 Hz, 4 dB | 3.1e-2 | 2.8e-2 | 3.0e-2 |

No cost on noise-only channels; on fading channels the differences are within what six
messages can resolve, except possibly 2400S. To be measured properly (more messages, and
with the receiver's filter matched) before D5 is settled.

## 6. Open questions and facts still needed

- **Pat's ARDOP driver**: which commands it sends, how it parses `CONNECTED`'s bandwidth
  and `ARQBW`, and what it does with an unknown value; the same tested (not read) for
  Winlink Express.
- **The -26 dB bandwidth on a real transmitter** at roll-off 0.15 (our numbers are at
  audio), and the receiver's cost of the narrower pulse, measured properly (D5).
- **Broadcast mode names**: our own `FECMODE` values, and what to do with ARDOP's.
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

1. Read Pat's ARDOP driver against §5's table; settle the bandwidth report and the
   broadcast mode names.
2. Make the transmit roll-off a setting and measure what 0.15 costs (D5).
3. Prototype frames and selective-repeat ARQ sessions in `tools/toystack.py` over the
   channel simulator, with B2F-sized traffic, before writing milestones.
4. Then write the modem program's milestones in PLAN.md: audio and keying, the host
   interface, the session layer, B2F, the TUI.
