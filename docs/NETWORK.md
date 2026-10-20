# The network stack: goals, decisions, open questions

Record of the network-stack discussion (2026-10-19) that PLAN.md §6 scheduled before the
modem program. It replaces that agenda as the place where the stack's direction is kept.
The modem program's milestones (P1-P8, 2026-10-20) are in PLAN.md §6; the open questions in
§6 below are settled by the milestones that name them.

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
- no encryption in the amateur profile, ever (encryption is an `authorized`-only item, D7);
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

**D5 [DECIDED] A narrower pulse for amateur use.** Part 97's 2.8 kHz limit on HF data
(confirmed by the user, 2026-10-19), measured at -26 dB, is exceeded by our roll-off of 0.25
(3.0 kHz). Since 2026-10-20 the default on both sides is 0.15, with the transmit filter
lengthened to 16 symbols each side (2749 Hz at audio, a 51 Hz margin instead of 20); wider
roll-offs (0.25, 0.35) need the `authorized` profile (D7). Measured: no cost to our receiver
from 0.15 to 0.35, matched or not (docs/PERFORMANCE.md, roll-off). Still to check: the
on-air spectrum (§6). Other 110 modems recommend wider pulses still
(Appendices C and D of 110C suggest a roll-off of 0.35), so a third-party 110 modem may not
be usable on US amateur HF as it stands.

**D6 [PROPOSED] Bandwidth through the host interface.** Refuse ARDOP bandwidths below
2000 Hz; accept `2000MAX` and `2000FORCED` as the widest a host can ask for; answer the
`ARQBW` query with the accepted ARDOP value; report the true bandwidth (2800) in `CONNECTED`.
Pat parses `ARQBW` replies strictly and never parses `CONNECTED`'s bandwidth (§5), so this
works with Pat; Winlink Express must be tested. Documented as a deviation from ARDOP's
meaning of `2000`.

**D7 [DECIDED] Two operating profiles** (`m110c.profile`, `--profile`), 2026-10-20:

- `amateur`, the default: every setting kept within amateur limits, US rules as the
  reference. A setting beyond them is refused, with a message naming the limit and the
  profile that lifts it. Today it limits the transmit roll-off to 0.15 (D5); the link stack's
  limits (identification, automatic operation, bandwidth through the host interface) will
  join it as they are built.
- `authorized`: every limit lifted, the full breadth of 110C's settings and of the link
  stack. For stations whose licence or authorisation covers what they transmit (MARS,
  SHARES, government, commercial or experimental stations, other countries' rules). Every
  use prints a notice, even with `--quiet`: all safeties are off; the operator, not the
  software or its authors, must make sure their licence or authorisation covers every
  band, frequency, bandwidth, mode and kind of operation; no warranty.

Receiving is not limited by either profile. **Encryption (2026-10-20): `authorized` only,
to be designed later.** Amateur rules forbid it, so the amateur profile never offers it;
services such as MARS and SHARES may permit or require it. Nothing is designed yet: which
layer it belongs to, which algorithms, key management, and how its absence is guaranteed in
the amateur profile (a build without it, or a refusal at run time) are open questions.

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
| Broadcast: `FECSEND`, `FECREPEATS <0-5>`, `FECMODE <ARDOP mode name>`; data tagged `FEC` or `ERR` | `FECMODE`'s values are ARDOP's waveforms. Pat never uses broadcast, so no host constrains it yet: our own names (a 110 rate and interleave), and a `FAULT` for ARDOP's |
| Bandwidth: `ARQBW <200\|500\|1000\|2000>MAX\|FORCED`; `REJECTEDBW`; the bandwidth in `CONNECTED` | The one real mismatch: 110 has one bandwidth, about 2.8 kHz (D5), wider than ARDOP's widest setting. Settings of 200-1000 Hz are refused (`FAULT`, or `REJECTEDBW` on a connection), never ignored: hosts use them to keep within band plans. `2000MAX` and `2000FORCED` are accepted as "the widest the host can ask for", and the documentation says that 110 then occupies 2.8 kHz. `CONNECTED` reports the true bandwidth; the `ARQBW` query answers with an ARDOP value (D6) |
| Radio control: `RADIO*` (frequency, PTT port, filters, models) | Optional in ARDOP too; out of scope at first (a `FAULT`). Hamlib could serve it later |
| Data port: host to modem `D:` + 2-byte length + bytes; modem to host `d:` + length + `ARQ`, `FEC`, `ERR` or `IDF` + bytes | Direct (over TCP without the prefixes: see below) |

Two cautions. First, the document is "Rev 0.2" and partly provisional, and deployed modems
and hosts differ from it (below). Second, Winlink Express is closed: compatibility with it
can only be tested, not read.

### What Pat actually sends and expects

Pat drives ARDOP through the ARDOP package of its library, wl2k-go (`transport/ardop`, MIT
licence, read at commit `efde6fb`, October 2025; Pat itself at `2e6a8d1`, April 2026). Read
for behaviour only; nothing is copied. Where it differs from the 2017 document, the code is
what deployed modems answer to.

**Framing over TCP.** No `C:`, `c:`, `D:` or `d:` prefixes and no CRCs (those are the serial
form's). Commands and replies are text lines ending in `\r`. Data from host to modem: a
2-byte big-endian length, then the bytes. Data from modem to host: a 2-byte big-endian length
counting what follows, then `ARQ`, `FEC`, `ERR` or `IDF`, then the bytes.

**What Pat sends.** On opening: `INITIALIZE`, `STATE` (and `CODEC TRUE` if the modem is
`OFFLINE`), `PROTOCOLMODE ARQ`, `ARQTIMEOUT 90`, `LISTEN FALSE` (the same default as D1),
`MYCALL`, `GRIDSQUARE`, then from its configuration `ARQBW`, `CWID`, and `VERSION` (also
used as a liveness check). When dialling: `ARQBW` (query, then set) only if a bandwidth is
given, then `ARQCALL <call> 10`. Also `MYAUX`, `SENDID` (a periodic beacon), `DISCONNECT`,
`ABORT`. Never `FECSEND` or anything about broadcast, `PING`, `BREAK`, or the radio commands.

**Behaviour a modem must have**, found in the code and not in the document:

- *Every command is answered by a line starting with the same command word* (optionally
  followed by `now`), or by `FAULT <text>`. Pat waits for one or the other.
- *A `BUFFER <n>` line after every block of data written.* Pat's write call does not
  return until one arrives, and its flush before disconnecting waits for `BUFFER 0`.
- *Received data in chunks well below 4096 bytes.* The driver hands each chunk whole to a
  reader whose buffer is typically 4096 bytes, and stops with an error if a chunk does not
  fit. ARDOP's frames are much smaller; ours should be delivered in pieces of at most a
  kilobyte.
- *`ARQ` data only while connected* (Pat discards it otherwise).
- *For an incoming connection, `TARGET <call>` before `CONNECTED <call> <bw>`.* Pat ignores
  a `CONNECTED` without one.
- *While dialling, a `NEWSTATE DISC` means the connection failed.* Pat reads only the
  callsign from `CONNECTED`; the bandwidth field is never parsed as a number.
- *The `ARQBW` query must answer with one of ARDOP's eight values* (`200MAX` ...
  `2000FORCED`); Pat parses it and fails the dial otherwise.
- *ID frames as data tagged `IDF`, containing `CALL:[GRID]`*, which Pat uses for its list
  of stations heard.
- *Timeouts on closing.* Pat waits up to 30 s for `BUFFER 0`, then sends `DISCONNECT` and
  waits up to 30 s for `DISCONNECTED` or `NEWSTATE DISC`, then sends `ABORT`. At 75 or
  150 bps 30 s is only 280-560 bytes, so a session's tail could be cut; B2F's own closing
  handshake normally leaves little queued by then. To be tested.
- *`PTT TRUE|FALSE` and `BUSY TRUE|FALSE` as asynchronous lines.* Pat keys the radio itself
  when configured to, and waits for a clear channel before dialling.

**Bandwidth, in practice.** Pat's default configuration sets `ARQBW 500MAX` on opening and
treats a refusal as fatal, and its list of gateway channels carries each gateway's ARDOP
bandwidth into the dial (for example `bw=500MAX`). With D6, a Pat user sets
`arq_bandwidth` to `2000MAX` (or leaves it empty) for this modem, and dials a 500 Hz ARDOP
channel only by choosing to. This is the refusal working as intended: nobody puts a
2.8 kHz signal on a channel listed as 500 Hz without deciding to.

**Frequency.** Pat converts a listed channel frequency to a dial frequency by subtracting
1500 Hz for ARDOP, the centre of ARDOP's audio. The 110 carrier is at 1800 Hz, so on a dial
frequency set by Pat our signal is centred 300 Hz above the listed channel. Moving the
carrier to 1500 Hz would put the 2.8 kHz signal at about 100-2900 Hz, at the edges of an SSB
transmitter's passband **[OPEN]**: keep 1800 Hz and document the offset, or offer a 1500 Hz
carrier setting for operators whose radios pass it.

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

What the narrower pulse costs our receiver: nothing measurable, from 0.15 to 0.35, matched
or not (docs/PERFORMANCE.md, roll-off, twelve messages per point; a first look with six
had hinted at a cost at 2400S on fading, which the larger run did not bear out).

## 6. Open questions and facts still needed

- **Winlink Express**: the behaviour §5 found in Pat, tested (it cannot be read).
- **The carrier frequency**: 1800 Hz (110's) with a documented 300 Hz offset from ARDOP
  channel listings, or a 1500 Hz option (§5).
- **Closing at low rates**: whether Pat's 30-s flush and disconnect timeouts cut sessions
  at 75 and 150 bps.
- **The -26 dB bandwidth on a real transmitter** at roll-off 0.15 (our numbers are at
  audio). The receiver's cost is measured: none (D5).
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
- **Busy detection** of signals other than 110. The many-station model (§7) says a busy
  detector is needed in any net of more than a few stations, but not a sensitive one: one
  deaf below 6 dB did about as well as one hearing −3 dB. What it must have is a quiet
  period longer than any gap inside a session.

## 7. Prototype: selective-repeat ARQ sessions (2026-10-20)

`tools/arqsim.py` (an experiment, not the modem) holds sessions between two stations over
one continuous simulated channel (2 paths, 2 ms, 1 Hz), every transmission through the
modulator, the channel and the complete receiver. Its frame format is a draft (in the
tool's docstring): each transmission carries a header (both callsigns), an acknowledgement
(next expected offset, up to 8 received ranges beyond it, the SNR measured, the mode to send
in) and data frames up to a 20-s cap, each with its own CRC-32 and a byte-stream offset. The
traffic is a B2F-shaped session (handshake, proposals, two compressed messages from A, one
from B, closing lines; 5.9 kB in all), emulated by its turns and sizes. The turnaround is
assumed (0.5 s), not measured; four sessions per point, each on its own fading channel.

**Many frames per transmission against stop-and-wait** (fixed 1200S; stop-and-wait sends one
128-byte frame per transmission and waits for its acknowledgement, as ARDOP does):

| Channel SNR | Stop-and-wait | Selective repeat |
|---:|---:|---:|
| 9 dB | 310 s | 100 s |
| 15 dB | 260 s | 65 s |
| 21 dB | 253 s | 65 s |

Three to four times faster. With 110's preambles a transmission costs at least 1.8 s plus the
turnaround, so it must carry as much as it can (D3 as proposed).

**Short against long interleave** (selective repeat, 1200 bps): short 65-100 s, long
129-141 s, at every SNR from 9 to 21 dB. Long interleave's 4.8-s preamble and 4.8-s blocks
are paid on every transmission, and a B2F session is mostly short turns. Long interleave
belongs to bulk transfer, if anywhere (the toystack found the same: never under 9.6 s).

**Which rate delivers most, by channel SNR** (`arqsim.py goodput`: 20-s transmissions of
200-byte frames, short interleave):

| Channel SNR | Best rate | Delivered | Notes |
|---:|---|---:|---|
| 3 dB | 600S | 150 bps | 150S delivers every frame, 121 bps |
| 6 dB | 600S | 408 bps | 1200S 384 bps |
| 9 dB | 1200S | 808 bps | 2400S loses 88% of frames |
| 12 dB | 1200S | 1030 bps | 2400S as much, but half its frames lost |
| 15 dB and up | 2400S | 1676-2100 bps | |

With retransmission, a rate pays even while it loses 20-50% of frames: the thresholds are
6-10 dB lower than Table XVI's (which are for a bit error rate of 1e-5). 300 bps never won.
The receiver's SNR report reads about 3 dB below the channel on the probed modes, but at
75 bps (no probes) it saturates near 4-5 dB whatever the channel; a 75-bps station must step
up on delivered frames, not SNR.

**Fixed against adaptive rate.** The adaptive rule (`choose_arq`): the receiver proposes the
rate in each acknowledgement, from the measured SNR and the table above; up one rung at a
time and only after a transmission whose frames all arrived; down as far as the SNR says,
and at least one rung when half the frames were lost; starting at 600S.

| Channel SNR | Fixed 1200S | Adaptive |
|---:|---:|---:|
| 3 dB | 0 of 4 sessions complete | 3 of 4, 726 s |
| 6 dB | 210 s | 214 s |
| 9 dB | 100 s | 113 s |
| 15 dB | 65 s | 70 s |
| 21 dB | 65 s | 56 s |

Adaptive wins where a fixed rate is wrong (3 dB, 21 dB) and costs 5-15% in between, the
price of starting at 600S and climbing one rung at a time.

**Frame size** (`arqsim.py sweep --param frame_bytes`, adaptive rate, 20-s cap, B2F session):

| Channel SNR | 64 B | 128 B | 200 B | 400 B | 800 B |
|---:|---:|---:|---:|---:|---:|
| 6 dB | 185 s | 190 s | 214 s | 168 s | 315 s |
| 12 dB | 76 s | 88 s | 77 s | 76 s | 86 s |
| 18 dB | 68 s | 59 s | 63 s | 69 s | 64 s |

From 64 to 400 bytes the session times agree within the spread of four channels (individual
sessions range over 20-50% at each point); 800 bytes is clearly worse at 6 dB, where a
frame that size rarely survives a fade. Smaller frames lose less per error but pay 14 bytes
of overhead each (22% at 64 bytes), and the two balance. 200 bytes stays; anything from 128
to 400 would do.

**The transmission cap** (`sweep --param cap_s`; the B2F session and a one-way 12 kB
transfer; the cap's two other settings with `settle_cap_s`):

| Traffic, channel SNR | 5 s | 10 s | 20 s | 40 s | 10 s, then 40 s once settled |
|---|---:|---:|---:|---:|---:|
| B2F, 6 dB | 359 s | 229 s | 214 s | 235 s | 236 s |
| B2F, 18 dB | 68 s | 65 s | 63 s | 60 s | 65 s |
| 12 kB, 6 dB | none of 4 | 470 s | 359 s | 267 s | 321 s |
| 12 kB, 18 dB | 124 s | 81 s | 92 s | 107 s | 75 s |

5 s is too short everywhere: a transmission's fixed cost (preamble, acknowledgement, turnaround
and the reply, about 5 s in all) is then most of it. For B2F the cap hardly matters from 10 s
up, because its turns are small. For bulk data it pulls two ways: on a poor channel the rate
stays low and longer transmissions spread the fixed cost over more data (40 s best); on a
good one the adaptive rate climbs one rung per transmission, so long transmissions make it
climb slowly (10 s best). A cap that is 10 s until the peer has asked for the same rate twice
running and 40 s after (`settle_cap_s`) is fastest on the good channel and two thirds of the
way to the best on the poor one.

Throughput is not the only measure of the cap. On a shared amateur frequency a transmission
cannot be interrupted: at 40 s a station wanting to break in (or in trouble) waits far longer
than with ARDOP or VARA, whose frames last a few seconds. So the longest cap is a courtesy
and safety question as well; proposed: 10 s until settled, at most 30 s after, and a way for
the receiving station to ask for shorter transmissions (to be designed with the session
layer).

**The harsh channel** (2 paths, 5 ms, 5 Hz: a fade every few tenths of a second;
`goodput --channel harsh`):

| Channel SNR | Best rate | Delivered | Notes |
|---:|---|---:|---|
| 0 dB | 150S | 61 bps | |
| 3 dB | 300S | 190 bps | the only point where 300 bps wins |
| 6 dB | 600S | 494 bps | 1200S delivers 25% of its frames |
| 9 dB and up | 1200S | 868-1050 bps | |

2400S does not work on this channel: at most 57% of frames even at 24 dB. And the SNR the
receiver reports reads 6-10 dB below the channel instead of 3 (fast fading puts the
equaliser's tracking error into what it counts as noise); on 2400S it saturates near 5-6 dB.
A rule calibrated on the moderate channel therefore keeps proposing 2400S on the harsh one
when the SNR is high, loses half the frames, steps down and tries again.

Two changes to the adaptive rule, from what this showed (`arqsim.py`, design `sr-start1200`):

- *Back-off.* A step up whose transmission lost 10% or more of its frames is a failed probe:
  the rate goes back to the one that worked before it (not as low as the SNR measured on
  the failed transmission says, which on fast fading reads several dB low), and the failed
  rate is not tried again for 3 transmissions, doubling at each further failure (up to
  24), and reset by a clean step up. Losses at a rate already in use do not count.
- *Start at 1200S* instead of 600S, the best fixed rate in every condition between the
  extremes.

The B2F session, median of four (the growing cap in all adaptive designs):

| Condition | Fixed 600S | Fixed 1200S | Adaptive, start 600S | Adaptive, back-off, start 1200S |
|---|---:|---:|---:|---:|
| Moderate, 3 dB | | 0 of 4 complete | 1 of 4 | 2 of 4, 838 s |
| Moderate, 9 dB | | 100 s | 121 s | 102 s |
| Moderate, 21 dB | | 65 s | 59 s | 52 s |
| Harsh, 6 dB | 130 s | 343 s | 129 s | 142 s |
| Harsh, 12 dB | 125 s | 66 s | 81 s | 85 s |
| Harsh, 18 dB | 125 s | 65 s | 86 s | 89 s |

(Within a point the four sessions spread over 20-50%, so differences under about 15% are not
resolved.) Adaptive is as good as the best fixed rate, or better, everywhere except the harsh
channel above 9 dB, where it is 20-30% behind fixed 1200S: it still probes 2400S, which never
works there. Better back-off will not fix that; knowing the channel will. The receiver could
report how fast the channel is changing (the equaliser sees it), and 2400S could be ruled out
on fast fading. Every fixed rate fails somewhere: 1200S at 3 dB moderate and 6 dB harsh, 600S
by half everywhere above.

**Drifting conditions** (moderate channel; SNR falling 18 to 3 dB or rising 3 to 18 dB over
three minutes, or a random walk around 12 dB; `compare --drift fall rise walk --traffic b2f
bulk`): the adaptive design (start 600S, growing cap) and fixed 1200S were within each
other's spread in all six combinations (B2F 65-145 s, 12-kB transfers 100-185 s), and fixed
600S was 2-4 times slower. No session of either failed: when the SNR dips, selective repeat
waits it out, and these sessions were short enough to end before the worst of the fall. The
drift scenarios did not separate the designs; the static extremes above did.

**Lost transmissions, timeouts and lost acknowledgements** (designs `rt-*`). Until now a
transmission nobody heard cost only its air time, and the other station then went anyway,
though it could not have known its turn had come. Now the turns are as a real protocol would
take them: the caller (A) transmits; the answering station (B) replies only to what it
understood; if B heard nothing, A waits out a timeout (3 s: turnaround, a short preamble,
detection margin) and tries again; if B replied but A could not read the reply, A tries
again once the channel is clear; after 8 tries in a row without a readable reply A gives up.
Only A retries, so the two never time out into each other. Telling an unreadable reply from
silence assumes a busy detector, which is still to be built (§6).

Two retry policies after an unreadable reply: *resend* the data still unacknowledged, or
*poll*, a short control transmission asking B to repeat its acknowledgement (B very likely
has the data already). After silence A always resends. And a second copy of the ACK frame
after the data (`ack_copies`), against a fade that takes the acknowledgement but spares the
data around it, a case in which the receiver of that transmission learns nothing about what
it sent and sends it all again.

| Condition (four sessions each) | Optimistic turns | Realistic, resend | Realistic, poll | Poll, ACK twice |
|---|---:|---:|---:|---:|
| B2F, moderate, 3 dB | 2 of 4, 838 s | 2 of 4, 806 s | 2 of 4, 694 s | |
| B2F, moderate, 6 dB | 197 s | 199 s | 199 s | 223 s |
| B2F, moderate, 9 dB | 102 s | 102 s | 102 s | 83 s |
| B2F, harsh, 6 dB | 142 s | 138 s | 138 s | 138 s |
| B2F, harsh, 12 dB | 85 s | 85 s | 85 s | 76 s |
| 12 kB, moderate, 6 dB | | 367 s | 362 s | 330 s |
| 12 kB, harsh, 6 dB | | | 235 s | 244 s |

Whole transmissions are rarely lost: from 6 dB up, B missed A's transmission at most 4 times
in four sessions, and A failed to read B's reply at most 10 times (bulk, 6 dB). The 110
preamble is far more robust than the data behind it. So realistic turn-taking costs little
above 3 dB, and polling helps only where losses are common (3 dB: 694 s against 806 s).
Lost acknowledgements are commoner, and the second copy cut them from 22 to 6 over the
eight conditions measured (10 to 4 in the worst, B2F at 6 dB), for about 40 bytes per data
transmission; session times moved both ways within the spread (once one acknowledgement
differs, a session takes a different course). No session deadlocked, and giving up works as
designed (unit tests). Proposed: realistic turns with the caller retrying, polling after an
unreadable reply, and the ACK sent twice when a transmission carries data.

Not modelled here: other stations (see "Many stations on one frequency" below); a busy
detector's own misses (which would turn an unreadable reply into silence, and a retry into
a collision); and the closing exchange of a session.

**A better start** (designs `st-*`, against rt-ack2, which starts at 1200S and climbs one
rung per report). Two ideas:
- *Jump*: the first report from each side goes straight to the rate its SNR supports.
- *Remember*: start at the rates the last session with the same station ended on. Here
  that is a session just before, on the same conditions: the best case for memory.

| Condition (four sessions each) | rt-ack2 | Jump | Remember | Remember, never below 1200S |
|---|---:|---:|---:|---:|
| Moderate, 3 dB | 3 of 4, 724 s | 3 of 4, 724 s | 2 of 4, 692 s | |
| Moderate, 9 dB | 83 s | 83 s | 107 s | 83 s |
| Moderate, 21 dB | 52 s | 52 s | 37 s | 37 s |
| Harsh, 6 dB | 138 s | 138 s | 143 s | 138 s |
| Harsh, 18 dB | 78 s | 78 s | 74 s | 74 s |

Jumping changed nothing, and could not have. From 1200S the only rung up is 2400S, which the
one-rung climb reaches in one report anyway, and the rule never limited going down. I should
have seen that before measuring it. Remembering saves about 30% at 21 dB, where the session
starts at 2400S instead of spending its first data transmissions at 1200S. It lost at 9 dB,
on one session of four. There the last session had ended at 600S, and the new one started
there; the other three remembered 1200S, the same as the fixed start. With four sessions a
point, one slower session moves the median. So: remember, but only upward. Start at the remembered rate when it is
higher than 1200S, else at 1200S. That kept every gain and none of the losses.

Not measured: memory that has gone stale. Starting too high costs one failed transmission,
then the back-off falls to the rate below. Starting too low cannot happen with the floor.
Proposed: start at 1200S, or the last rate used with the same station if higher.

**A fading-rate report from the receiver.** The equaliser re-estimates the channel for every
unit of data. How much the estimate changes over a lag tells how fast the channel fades.
`EqualizingDemodulator.fading_hz` compares estimates about 0.1 s apart (240 symbols) with
estimates one unit apart, which removes the estimation noise. Under a Gaussian Doppler
spectrum the correlation over the lag is ρ = exp(−2π²σ²τ²); it reports the fading bandwidth
2σ in Hz. The report is:
- `Message.fading_hz`, printed by `m110c-rx` beside the SNR;
- `None` without the equaliser, or with fewer than 20 pairs of estimates;
- counted only while the equaliser fits the signal (half or more of the unit), so noise
  after a message does not count.

Measured against the simulator, 2400-75 bps and 6-20 dB:
- within about 10% from 0.5 to 5 Hz, a little high for slow fading at low SNR;
- saturating near 6-7 Hz for faster fading;
- a fair reading already from 150 bytes at 1200 bps (`tests/unit/test_fading_rate.py`).

In arqsim sessions it read 4-5 Hz on the harsh channel (5 Hz) and about 1 Hz on the
moderate one. One short, failed 2400S transmission read 2.7 Hz, so the limit has a margin.
The rule (design `fd-limit`): while the last reading is above 3 Hz, ask for nothing faster
than 1200S.

| Condition (six sessions each) | rt-ack2 | With the fading rule |
|---|---:|---:|
| Harsh, 9 dB | 123 s | 123 s |
| Harsh, 15 dB | 66 s | 61 s |
| Harsh, 21 dB | 77 s | 61 s |
| Moderate, 15 dB | 72 s | 72 s |
| Moderate, 21 dB | 48 s | 48 s |

On the harsh channel the adaptive design now matches fixed 1200S, its best rate, where
before it was 20-30% behind. It stops probing 2400S, which never works there. On the
moderate channel the rule never fired. Proposed: the receiver reports the fading rate
beside the SNR, and the rate rule caps at 1200S above 3 Hz.

**Many stations on one frequency** (`tools/netsim.py`). Everything above is two stations
alone. A net has many, on one frequency, some unable to hear each other, and no busy
detector exists yet. Twenty stations over hours are far too slow to put through the modem,
so this is an event-level model built on what arqsim measured. Each transmission has its
real air time and arqsim's frames. Each frame arrives with the probability `goodput`
measured for its mode, at the SINR its receiver sees over it. Every overlapping
transmission counts as noise at full power.

Other assumptions:
- A station transmitting hears nothing.
- A receiver already following one signal misses a later preamble ("capture").
- Sessions follow rt-ack2 at the best rate for the link's SNR.
- A station holds one session at a time, and ignores other callers until it is free (or
  60 s after it last heard its caller).
- A caller that gives up tries again 1-5 minutes later, three attempts in all.

Traffic is arqsim's B2F session (about 6 kB), offered at random (Poisson) by every calling
station. Two topologies:
- *Hub*: clients calling a gateway everyone reaches at 6-24 dB. Two clients hear each
  other at 0-24 dB, or not at all, with the chance "hidden".
- *Mesh*: any two stations at 6-24 dB, or not at all; sessions between random pairs.

Channel access is the variable under test:

| Access | Rule before a call, and before retrying after a failure |
|---|---|
| ALOHA | none: no busy detector |
| LBT | listen before talk: nothing detected (above −3 dB) for 4 s, longer than a turnaround or a timeout, so a session in progress keeps the channel; else wait 1-10 s and look again |
| LBT, poor detector | the same, hearing only signals above 6 dB |
| LBT, instant | the same detector, checking only the moment before transmitting |
| LBT + NAV | LBT, and a station that decodes another session's HEAD defers for 40 s ("virtual carrier sense") |
| LBT, long | a 35-s quiet period: longer than any transmission, so one end of a session in progress is always heard |

As a check, one link alone takes 37, 55, 94 and 151 s at 21, 15, 9 and 6 dB in this model.
arqsim with the real modem takes 52, about 68, 83 and 223 s (37 s at 21 dB once the rate
is remembered, below). The model is 0-30% optimistic: its rate has already settled and its
channel has no long fades. Its comparisons between access rules are what it is for.

One gateway, clients at one session an hour each, 30% of client pairs hidden (eight
four-hour runs per point; sessions done, then the delay from offer to completion, median
and 90th percentile, in s):

| Clients | ALOHA | LBT | LBT, poor detector | LBT + NAV | LBT, long |
|---:|---|---|---|---|---|
| 5 | 100%, 56 / 146 | 100%, 52 / 135 | 100%, 52 / 135 | 100%, 55 / 139 | 100%, 54 / 138 |
| 10 | 98%, 75 / 284 | 100%, 67 / 163 | 100%, 67 / 161 | 100%, 80 / 189 | 100%, 76 / 186 |
| 20 | 92%, 87 / 429 | 100%, 67 / 179 | 100%, 67 / 181 | 100%, 84 / 252 | 100%, 83 / 247 |
| 30 | 67%, 164 / 595 | 100%, 93 / 340 | 98%, 94 / 394 | 100%, 164 / 690 | 100%, 169 / 758 |

The channel was busy 8%, 16%, 30% and 49% of the time with LBT. Load, ten clients and one
gateway, 30% hidden:

| Sessions offered per hour | ALOHA | LBT | LBT + NAV | Channel busy (LBT) |
|---:|---|---|---|---:|
| 20 | 84%, 108 / 495 | 100%, 81 / 266 | 100%, 112 / 346 | 36% |
| 30 | 63%, 228 / 704 | 99%, 115 / 570 | 100%, 193 / 957 | 56% |
| 40 | 41%, 335 / 945 | 95%, 215 / 2666 | 100%, 518 / 3033 | 69% |
| 51 | 27%, 542 / 1940 | 92%, 700 / 5487 | 100%, 1141 / 5769 | 80% |

What this says:

- **A busy detector is not optional for a net.** Without one, a frequency serves a handful
  of stations. From about ten upward, sessions start to fail, and from twenty a tenth or
  more fail even after three attempts. The losses compound: a caller that finds its gateway
  busy, or collides, retries eight times over about 40 s, damaging the session in progress.
- **Plain listen-before-talk carries the load up to about 30 sessions an hour on one
  frequency** (channel about half busy, 90% of sessions done within 6 minutes), and
  degrades gracefully above it. The quiet period matters: checking only the instant before
  transmitting lost 8% of sessions at 30 clients (1.43 attempts per session against 1.09).
  The channel looks free in a session's own turnaround and timeout gaps.
- **The detector need not be sensitive.** A detector deaf below 6 dB did almost as well as
  one hearing −3 dB. In the model a station's detectability at the caller and its
  interference at the receiver are drawn independently, so this is not built in. A simple
  detector (§6) should do.
- **Hidden stations cost little in a hub.** Every client hears the gateway, which speaks at
  least every ~33 s of a session (the cap), so the exposed window is a hidden client's own
  turn. With 60% of client pairs hidden, LBT needed at most 1.2 attempts per session (30 clients). In the
  mesh with 20 stations, LBT completed every session with 0-60% of pairs hidden (median
  68-75 s), against 84-98% for ALOHA.
- **Virtual carrier sense and the long quiet period do what they are for, at a price.**
  Fewer attempts, less air per session (65 s against 81 s at 51 sessions an hour), and no
  failed session even far past saturation. But every caller waits longer: median delays
  are 1.2-2 times plain LBT at every load measured. A HEAD frame names its session, so a
  deferral rule costs nothing to add later, as an overload mode if a busy net needs one.
- **The air time of one frequency is the limit**, not the protocol. About 60 s of air per
  B2F session means one frequency cannot pass 60 sessions an hour, and delays grow from
  half that. A second gateway on the same frequency helped, but only through better links.
  Clients reaching the nearer of two gateways needed 51 s of air per session against 71 s.
  At 40 sessions an hour, 99% were done (90% within 10 minutes), against 95% (44 minutes).
  Busy time still equalled the sum of the sessions' air time: the two never worked in
  parallel, because the gateways hear each other. More traffic needs better links or more
  frequencies: Winlink's channel lists, or scanning.

The conclusions survive dropping the capture rule (ALOHA does a little better; LBT the
same). The model's limits:
- interference is treated as noise;
- fading is averaged into the frame table;
- SNRs are random rather than propagated;
- the busy detector is perfect above its threshold, with no false alarms;
- there are no other users of the frequency (on amateur bands there will be, and the
  detector must hear them too: §6).

Proposed: listen before talk with a quiet period longer than any gap inside a session (4 s
with these timings), a random back-off, and in-session turns that do not listen. Deferring
on another session's HEAD stays an option, not the default.

**Still to measure** before the link layer is specified, all of it needing radios or a
third station:
- the real turnaround;
- a real busy detector (its misses, its false alarms, and other modes on the frequency);
- a remembered rate gone stale.

## 8. Next steps

1. Done: Pat's ARDOP driver read (§5); D6 proposed. Its fbb package (B2F) is the next
   thing to read when B2F comes up.
2. Done: the roll-off is a setting on both sides, 0.15 (span 16) is the default and costs
   nothing measurable (D5); wider settings need the `authorized` profile (D7).
3. Done: `tools/arqsim.py`, selective-repeat ARQ sessions with B2F-shaped traffic, and
   `tools/netsim.py`, many stations on one frequency (§7). What remains needs radios.
4. Done: the modem program's milestones, P1-P8, in PLAN.md §6: real-time core, virtual
   air, keying, busy detector, link layer (docs/LINK.md), the host interface, the TUI, more
   interfaces. Our own B2F is not among them: Pat brings it (P6).
