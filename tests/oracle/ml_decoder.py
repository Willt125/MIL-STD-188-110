"""Brute-force maximum-likelihood decoder: try every possible message.

For a message of n bits followed by the 6-bit zero tail that returns the encoder to its
starting state, encode all 2**n candidates with the literal oracle encoder and pick the
one whose codeword best matches the soft bits. "Best" is the largest correlation
sum(L_i * (+1 if c_i == 0 else -1)), which for Gaussian noise is maximum likelihood.
Only practical for n up to about 14.
"""

import itertools

from .conv_encoder import encode

TAIL = 6


def decode(soft, n_message_bits):
    best_metric, best_message = None, None
    for message in itertools.product((0, 1), repeat=n_message_bits):
        codeword = encode(list(message) + [0] * TAIL)
        metric = sum(L * (1 - 2 * c) for L, c in zip(soft, codeword, strict=True))
        if best_metric is None or metric > best_metric:
            best_metric, best_message = metric, list(message)
    return best_message
