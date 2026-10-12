"""Receiver.

The receive chain mirrors the transmitter block for block:

    samples -> frontend (carrier removal, matched filter, symbol sampling)
            -> decode   (descrambling, soft demapping, deinterleaving, Viterbi)
            -> eom      (end-of-message detection)
            -> framing  (bits -> bytes)

M7 provides everything after symbol timing is known: :class:`~m110c.rx.genie.GenieReceiver`
is told where the symbols are, what the carrier is and which mode is in use. M8 added
acquisition and tracking (:mod:`.acquire`, :mod:`.track`, :mod:`.receiver`), M10 the
multipath channel estimator and equaliser (:mod:`.equalize`).
"""
