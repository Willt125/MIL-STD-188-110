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

Receiving is not limited by either profile. One limit stays outside both: no encryption
(D1), unless decided otherwise.

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
- **Busy detection** of signals other than 110.

## 7. Next steps

1. Done: Pat's ARDOP driver read (§5); D6 proposed. Its fbb package (B2F) is the next
   thing to read when B2F comes up.
2. Done: the roll-off is a setting on both sides, 0.15 (span 16) is the default and costs
   nothing measurable (D5); wider settings need the `authorized` profile (D7).
3. Prototype frames and selective-repeat ARQ sessions in `tools/toystack.py` over the
   channel simulator, with B2F-sized traffic, before writing milestones.
4. Then write the modem program's milestones in PLAN.md: audio and keying, the host
   interface, the session layer, B2F, the TUI.
