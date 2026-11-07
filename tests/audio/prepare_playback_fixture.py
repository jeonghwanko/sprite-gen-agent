# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (text files read and written as UTF-8; see NOTICE).
"""Create one synthetic encoded bundle for render_playback.mjs; no provider calls."""
import json
from pathlib import Path
import sys
import wave

import numpy as np

from sprite_gen.audio import playback, processing


def prepare(directory):
    directory = Path(directory)
    directory.mkdir(parents=True)
    source = directory/'source.wav'
    frames = 144003  # fractional destination frame at 44.1 kHz
    t = np.arange(frames)/48000
    samples = np.stack([.1*np.sin(2*np.pi*317*t+.5), .3*np.cos(2*np.pi*73*t)], axis=1)
    with wave.open(str(source), 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes((samples*32767).astype('<i2').tobytes())
    details = processing.export(source, directory, target_lufs=-20, loop=True, crossfade=.25)
    manifest = {'kind':'sprite-gen-sound', 'version':playback.VERSION, 'audio_kind':'bgm',
                'event':'synthetic.loop', 'loop':True, 'target_lufs':-20, **details}
    (directory/'sound.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding="utf-8")
    return manifest


if __name__ == '__main__':
    prepare(sys.argv[1])
