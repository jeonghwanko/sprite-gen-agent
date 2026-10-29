# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (portable player bytes across checkout line endings; see NOTICE).
"""Frame contract and the browser player shipped with every audio pack."""
from __future__ import annotations

import hashlib
from pathlib import Path

VERSION = 2
MIN_PLAYBACK_RATE = 8000
PLAYER_PATH = Path(__file__).with_name('sprite-gen-audio-v2.mjs')


def validate(value, loop):
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ValueError('audio playback requires frame contract version 2')
    fields = ('sample_rate', 'start_frame', 'end_frame', 'overlap_frames')
    if any(type(value.get(k)) is not int for k in fields):
        raise ValueError('audio playback coordinates must be integer frames')
    rate, start, end, overlap = (value[k] for k in fields)
    if not 8000 <= rate <= 192000 or not 0 <= start < end <= 601*rate:
        raise ValueError('invalid audio playback interval')
    if (loop and not 2 <= overlap < (end-start)/2) or (not loop and overlap != 0):
        raise ValueError('invalid audio playback overlap')
    # Reserve 3.5 destination frames: interval truncation (<1), final-loop
    # rounding (<=0.5), and a decoder shortfall (1) still leave >=2 blend frames.
    if loop and 2*overlap*MIN_PLAYBACK_RATE < 7*rate:
        raise ValueError(f'audio playback overlap too short for {MIN_PLAYBACK_RATE} Hz decoding')
    return {'version': VERSION, **{k: value[k] for k in fields}}


def duration(value):
    return (value['end_frame']-value['start_frame']-value['overlap_frames'])/value['sample_rate']


def player():
    # Ship canonical source bytes even when Git checks this text out as CRLF on
    # Windows. The manifest hashes the bytes we actually ship, not the checkout.
    data = PLAYER_PATH.read_bytes().replace(b'\r\n', b'\n')
    return {'path': PLAYER_PATH.name, 'sha256': hashlib.sha256(data).hexdigest(),
            'export': 'createAudioSource'}, data
