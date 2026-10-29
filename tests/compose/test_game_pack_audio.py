# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (cross-platform audio helper contract; see NOTICE).
import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from sprite_gen.audio import processing
from sprite_gen.compose.game_pack import pack


def test_audio_pack_bytes_do_not_depend_on_player_checkout_line_endings(tmp_path, monkeypatch):
    from sprite_gen.audio import playback
    source = playback.PLAYER_PATH.read_bytes().replace(b'\r\n', b'\n')
    helper_dir = tmp_path / 'helper'
    helper_dir.mkdir()
    helper = helper_dir / playback.PLAYER_PATH.name
    monkeypatch.setattr(playback, 'PLAYER_PATH', helper)
    path, _ = with_sound(tmp_path)
    helper.write_bytes(source)
    pack(path, tmp_path / 'lf')
    helper.write_bytes(source.replace(b'\n', b'\r\n'))
    result = pack(path, tmp_path / 'crlf')
    assert {p.name: p.read_bytes() for p in (tmp_path / 'lf').iterdir()} == {
        p.name: p.read_bytes() for p in (tmp_path / 'crlf').iterdir()}
    shipped = (tmp_path / 'crlf' / helper.name).read_bytes()
    assert shipped == source
    assert result['audio']['hit']['player']['sha256'] == hashlib.sha256(shipped).hexdigest()


def inputs(tmp_path):
    Image.new('RGBA', (8, 8), 'red').save(tmp_path/'image.png')
    request = {'version':1, 'assets':[{'id':'hero', 'source':'image.png', 'display':{'width':8},
                                     'events':[{'frame':0, 'name':'combat.hit'}]}]}
    source = tmp_path/'audio'
    source.mkdir()
    files = {}
    for fmt in ('ogg', 'm4a'):
        data = b'synthetic-'+fmt.encode()
        (source/f'sound.{fmt}').write_bytes(data)
        files[fmt] = {'path':f'sound.{fmt}', 'duration_seconds':1, 'lufs':-18,
                      'true_peak_dbfs':-2, 'sha256':hashlib.sha256(data).hexdigest()}
    manifest = {'kind':'sprite-gen-sound','version':2,'event':'combat.hit', 'audio_kind':'sfx',
                'loop':False,'duration_seconds':1,'target_lufs':-18,'files':files,
                'recipe': {'processing_version':4},
                'normalization': {'pcm_lufs':-18, 'pcm_true_peak_dbtp':-2},
                'playback': {'version':2, 'sample_rate':48000, 'start_frame':0,
                             'end_frame':48000, 'overlap_frames':0}}
    (source/'sound.json').write_text(json.dumps(manifest))
    path = tmp_path/'request.json'
    path.write_text(json.dumps(request))
    return path, request, manifest


def test_optional_audio_preserves_image_only_bytes_and_packs_events(tmp_path):
    path, request, original = inputs(tmp_path)
    pack(path, tmp_path/'before')
    request['audio'] = []
    path.write_text(json.dumps(request))
    pack(path, tmp_path/'empty')
    assert {p.name:p.read_bytes() for p in (tmp_path/'before').iterdir()} == {
        p.name:p.read_bytes() for p in (tmp_path/'empty').iterdir()}
    request['audio'] = [{'id':'hit','source':'audio/sound.json'}]
    path.write_text(json.dumps(request))
    result = pack(path, tmp_path/'with')
    audio = result['audio']['hit']
    assert audio['event'] == result['animations']['hero']['events'][0]['name']
    assert (tmp_path/'with'/audio['files']['ogg']['path']).read_bytes() == b'synthetic-ogg'
    assert audio['files']['m4a']['path'] == 'audio-hit.m4a'
    assert audio['playback'] == original['playback']
    helper = audio['player']
    assert helper['path'] == 'sprite-gen-audio-v2.mjs'
    assert helper['export'] == 'createAudioSource'
    assert hashlib.sha256((tmp_path/'with'/helper['path']).read_bytes()).hexdigest() == helper['sha256']
    assert pack(path, tmp_path/'with') == result
    total = sum(p.stat().st_size for p in (tmp_path/'with').iterdir())
    with pytest.raises(ValueError, match='bytes'):
        pack(path, tmp_path/'small', max_bytes=total-1)
    assert not (tmp_path/'small').exists()


@pytest.mark.parametrize('defect', ['duplicate', 'traversal', 'digest', 'nonfinite', 'badloop', 'old', 'fraction', 'overlap',
                                    'prerule', 'quiet', 'norecipe', 'recipe2'])
def test_broken_audio_cannot_publish_a_partial_pack(tmp_path, defect):
    path, request, manifest = inputs(tmp_path)
    request['audio'] = [{'id':'hit','source':'audio/sound.json'}]
    if defect == 'duplicate': request['audio'] *= 2
    if defect == 'traversal': manifest['files']['ogg']['path'] = '../image.png'
    if defect == 'digest': manifest['files']['ogg']['sha256'] = '0'*64
    if defect == 'nonfinite': manifest['target_lufs'] = float('nan')
    if defect == 'badloop': manifest['loop'] = 'false'
    if defect == 'old': manifest['version'] = 1
    if defect == 'fraction': manifest['playback']['end_frame'] = 48000.5
    if defect == 'overlap': manifest['playback']['overlap_frames'] = 24000
    if defect == 'prerule': del manifest['normalization']
    if defect == 'quiet': manifest['files']['m4a']['lufs'] = -19.5  # under the target, its PCM not peak-limited
    if defect == 'norecipe': del manifest['recipe']
    if defect == 'recipe2': manifest['recipe']['processing_version'] = 2
    path.write_text(json.dumps(request))
    (tmp_path/'audio'/'sound.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError): pack(path, tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


# A version 1 pack with sound, packed by 2.44.0 (before request version 2): its file names, the
# manifest's exact bytes and the page's decoded pixels (encoded PNG/WebP bytes depend on the zlib and
# libwebp build). The sound files and the helper are compared byte for byte below.
V1_AUDIO_FILES = ['atlas-000.png', 'atlas-000.webp', 'audio-hit.m4a', 'audio-hit.ogg', 'game-pack.json',
                  'sprite-gen-audio-v2.mjs']
V1_AUDIO_MANIFEST_SHA256 = 'b79d31a4f38982f2c4a97b2aa5ddb4e77b545acee29210dac6a0e6649f9f5b67'
V1_AUDIO_PAGE_PIXELS_SHA256 = '834f7685ed723160d26784bb8ba1153e5f8ae56f5ddc8f51ebb0c54b1a63e067'


def with_sound(tmp_path):
    path, request, _ = inputs(tmp_path)
    request['audio'] = [{'id': 'hit', 'source': 'audio/sound.json'}]
    path.write_text(json.dumps(request))
    return path, request


def test_version_1_pack_with_sound_keeps_its_files_and_bytes(tmp_path):
    path, _ = with_sound(tmp_path)
    out = tmp_path/'out'
    result = pack(path, out)
    assert sorted(p.name for p in out.iterdir()) == V1_AUDIO_FILES
    manifest = (out/'game-pack.json').read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == V1_AUDIO_MANIFEST_SHA256
    assert json.loads(manifest)['version'] == 1
    with Image.open(out/'atlas-000.png') as page:
        assert hashlib.sha256(page.convert('RGBA').tobytes()).hexdigest() == V1_AUDIO_PAGE_PIXELS_SHA256
    for fmt in ('ogg', 'm4a'):
        assert (out/f'audio-hit.{fmt}').read_bytes() == f'synthetic-{fmt}'.encode()
    assert hashlib.sha256((out/'sprite-gen-audio-v2.mjs').read_bytes()).hexdigest() == result['audio']['hit']['player']['sha256']


def test_version_2_pack_with_sound_adds_only_the_tile_files(tmp_path):
    path, request = with_sound(tmp_path)
    pack(path, tmp_path/'v1')
    Image.new('RGBA', (8, 8), 'blue').save(tmp_path/'ground.png')
    request.update(version=2, tiles=[{'id': 'ground', 'source': 'ground.png', 'display': {'width': 16}}])
    path.write_text(json.dumps(request))
    result = pack(path, tmp_path/'v2')
    v1 = {p.name: p.read_bytes() for p in (tmp_path/'v1').iterdir()}
    v2 = {p.name: p.read_bytes() for p in (tmp_path/'v2').iterdir()}
    assert sorted(v2) == sorted([*v1, 'tile-ground.png', 'tile-ground.webp'])
    for name in ('audio-hit.ogg', 'audio-hit.m4a', 'sprite-gen-audio-v2.mjs', 'atlas-000.png', 'atlas-000.webp'):
        assert v2[name] == v1[name], name
    assert result['version'] == 2 and list(result['audio']) == ['hit']
    assert result['tiles']['ground']['image'] == 'tile-ground.png'


SOUND_ONLY_FILES = ['audio-hit.m4a', 'audio-hit.ogg', 'game-pack.json', 'sprite-gen-audio-v2.mjs']


@pytest.mark.parametrize('version,assets', [(1, []), (1, None), (2, []), (2, None)])
def test_a_sound_only_request_packs_the_manifest_sound_and_helper(tmp_path, version, assets):
    path, request = with_sound(tmp_path)
    request['version'] = version
    path.write_text(json.dumps(request))
    pictured = pack(path, tmp_path/'pictured')
    assert pictured['version'] == version and pictured['textures']
    if assets is None:
        del request['assets']
    else:
        request['assets'] = assets
    path.write_text(json.dumps(request))
    out = tmp_path/'sound'
    result = pack(path, out)
    assert sorted(p.name for p in out.iterdir()) == SOUND_ONLY_FILES
    # the manifest a pack with pictures has, its textures and animations empty
    assert result == {**pictured, 'textures': [], 'animations': {}}
    assert json.loads((out/'game-pack.json').read_bytes()) == result
    for name in ('audio-hit.m4a', 'audio-hit.ogg', 'sprite-gen-audio-v2.mjs'):
        assert (out/name).read_bytes() == (tmp_path/'pictured'/name).read_bytes(), name
    assert pack(path, out) == result


@pytest.mark.parametrize('document', [{'version': 1, 'assets': []}, {'version': 1, 'assets': [], 'audio': []},
                                      {'version': 1}, {'version': 2, 'assets': [], 'audio': []}, {'version': 2}])
def test_a_request_with_neither_pictures_nor_sound_is_refused(tmp_path, document):
    path = tmp_path/'request.json'
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='nothing to pack'):
        pack(path, tmp_path/'out')
    assert not (tmp_path/'out').exists()


def version_3_bundle(tmp_path, lufs=-18., spike=None, timescale=48000, **changes):
    """A sound made by processing version 3: real encodes of a synthetic tone, the file numbers that
    version's loudnorm check recorded, and no PCM loudness record. 48003 frames are no whole
    millisecond, so a millisecond AAC timescale cannot store them."""
    t = np.arange(48003) / 48000
    pcm = np.stack([np.sin(2*np.pi*440*t), np.sin(2*np.pi*440*t + .3)], axis=1)
    pcm *= 10 ** ((lufs - processing.measure(pcm)['lufs']) / 20)
    if spike:
        pcm[24000:24048] = spike
    path, request, manifest = inputs(tmp_path)
    files = {}
    for fmt, codec in (('ogg', ['-c:a', 'libopus', '-b:a', '160k']),
                       ('m4a', ['-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart',
                                '-movie_timescale', str(timescale)])):
        file = tmp_path/'audio'/f'sound.{fmt}'
        processing._ffmpeg(['-f', 'f32le', '-ar', '48000', '-ac', '2', '-i', 'pipe:0', *codec, str(file)],
                           pcm.astype('<f4').tobytes())
        files[fmt] = {'path':file.name, 'duration_seconds':1.02, 'lufs':-18.6, 'true_peak_dbfs':-2,
                      'encode_passes':1, 'pcm_peak_limit_dbtp':-2,
                      'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
    manifest['playback']['end_frame'], manifest['duration_seconds'] = 48003, 48003/48000
    manifest.update(files=files, recipe={'processing_version':3},
                    normalization={'method':'loudnorm-two-pass', 'peak_limit_dbtp':-2, 'gain_limiter_passes':0})
    for key, value in changes.items():
        manifest[key] = {**manifest[key], **value} if isinstance(value, dict) else value
    request['audio'] = [{'id':'hit','source':'audio/sound.json'}]
    path.write_text(json.dumps(request))
    (tmp_path/'audio'/'sound.json').write_text(json.dumps(manifest))
    return path


def test_a_version_3_sound_is_packed_by_the_target_it_was_made_for(tmp_path):
    result = pack(version_3_bundle(tmp_path), tmp_path/'pack')
    for file in result['audio']['hit']['files'].values():
        # its files are measured again over their frames, not taken from its loudnorm-era numbers
        assert abs(file['lufs'] + 18) <= .3 and file['lufs'] != -18.6
        assert -20 < file['true_peak_dbfs'] <= -1


# A processing_version that is neither 3 nor 4 is refused before any version 3 measuring;
# test_broken_audio_cannot_publish_a_partial_pack[recipe2] and [norecipe] own that.
@pytest.mark.parametrize('defect', ['quiet', 'peak-limited', 'hot', 'timescale'])
def test_a_version_3_sound_that_misses_its_rule_is_refused(tmp_path, defect):
    path = version_3_bundle(
        tmp_path, lufs=-21.5 if defect in ('quiet', 'peak-limited') else -18.,
        spike=.94 if defect == 'hot' else None,  # -0.5 dBTP while its manifest says -2
        timescale=1000 if defect == 'timescale' else 48000,  # the millisecond AAC duration of version 2
        **({'normalization':{'pcm_lufs':-21.5, 'pcm_true_peak_dbtp':-2}} if defect == 'peak-limited' else {}))
    with pytest.raises(ValueError, match={'quiet':'does not match', 'peak-limited':'does not match',
                                          'hot':'does not match', 'timescale':'container duration'}[defect]):
        pack(path, tmp_path/'bad')
    assert not (tmp_path/'bad').exists()
