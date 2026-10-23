"""Ableton -> AAF: el audio exportado debe sonar como en Ableton."""

import gzip

import aaf2
import numpy as np
import pytest
import soundfile as sf

from conversor.audio import prepare
from conversor.convert import convert
from conversor.formats import ableton

RATE = 48000


def clip_xml(
    start, end, file, *, warped=False, markers=((0, 0), (2, 1)), loop=None, content_start=0.0,
    transpose=0.0, warp_mode=4, gain=1.0, disabled=False,
):
    loop_start, loop_end = loop if loop else (content_start, content_start + 100)
    marker_xml = "".join(
        f'<WarpMarker Id="{i}" BeatTime="{b}" SecTime="{s}"/>' for i, (b, s) in enumerate(markers)
    )
    return f"""
       <AudioClip Id="0" Time="{start}">
        <CurrentStart Value="{start}"/><CurrentEnd Value="{end}"/>
        <Loop><LoopStart Value="{loop_start}"/><LoopEnd Value="{loop_end}"/>
         <StartRelative Value="{content_start - loop_start}"/><LoopOn Value="{'true' if loop else 'false'}"/></Loop>
        <Name Value="clip"/>
        <Disabled Value="{'true' if disabled else 'false'}"/>
        <SampleRef><FileRef><RelativePath Value="{file}"/></FileRef></SampleRef>
        <WarpMarkers>{marker_xml}</WarpMarkers>
        <IsWarped Value="{'true' if warped else 'false'}"/>
        <WarpMode Value="{warp_mode}"/>
        <PitchCoarse Value="{int(transpose)}"/><PitchFine Value="{round((transpose - int(transpose)) * 100)}"/>
        <SampleVolume Value="{gain}"/>
       </AudioClip>"""


def build_set(folder, clips, tempo=120):
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="11.0_433">
 <LiveSet>
  <Tracks>
   <AudioTrack Id="1">
    <Name><EffectiveName Value="Audio"/><UserName Value=""/></Name>
    <DeviceChain>
     <Mixer><Volume><Manual Value="1"/></Volume><Pan><Manual Value="0"/></Pan></Mixer>
     <MainSequencer><Sample><ArrangerAutomation><Events>{''.join(clips)}</Events></ArrangerAutomation></Sample></MainSequencer>
    </DeviceChain>
   </AudioTrack>
  </Tracks>
  <MasterTrack><DeviceChain><Mixer>
   <Tempo><Manual Value="{tempo}"/><AutomationTarget Id="8"/></Tempo>
   <TimeSignature><Manual Value="201"/><AutomationTarget Id="9"/></TimeSignature>
  </Mixer></DeviceChain></MasterTrack>
 </LiveSet>
</Ableton>"""
    path = folder / "Set.als"
    path.write_bytes(gzip.compress(xml.encode()))
    return path


def clicks(seconds, every, rate=RATE, channels=2):
    """Audio con un golpe corto cada `every` segundos."""
    audio = np.zeros((int(seconds * rate), channels), dtype=np.float32)
    for k in range(int(seconds / every) + 1):
        i = int(round(k * every * rate))
        audio[i : i + 48] = 0.9
    return audio


def tone(seconds, freq, rate=RATE):
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)[:, None].repeat(2, axis=1)


def click_times(audio, rate=RATE, threshold=0.3):
    level = np.abs(audio[:, 0])
    times, i = [], 0
    while i < len(level):
        if level[i] > threshold:
            # El pico del golpe, no el primer instante que supera el umbral.
            window = level[i : i + rate // 100]
            times.append((i + int(np.argmax(window))) / rate)
            i += rate // 10
        else:
            i += 1
    return times


def peak_frequency(audio, rate=RATE):
    spectrum = np.abs(np.fft.rfft(audio[:, 0] * np.hanning(len(audio))))
    return np.argmax(spectrum) * rate / len(audio)


def regions_of(path, tmp_path):
    project = ableton.read(path)
    prepared = prepare(project, tmp_path / "work")
    ((_, regions),) = prepared.tracks
    return project, prepared, regions


def test_unstretched_clip_references_original_file(tmp_path):
    sf.write(tmp_path / "a.wav", clicks(4, 0.5), RATE, subtype="PCM_24")
    path = build_set(tmp_path, [clip_xml(4, 8, "a.wav", warped=True, markers=((0, 0), (2, 1)), content_start=1)])
    _, prepared, (region,) = regions_of(path, tmp_path)
    assert prepared.rendered == 0
    assert region.file == tmp_path / "a.wav"
    assert (region.start, region.length, region.offset) == pytest.approx((2.0, 2.0, 0.5))


def test_warped_clip_is_stretched_to_session_tempo(tmp_path):
    # Material a 100 BPM (una negra = 0,6 s) en una sesión a 120 BPM.
    sf.write(tmp_path / "loop.wav", clicks(5, 0.6), RATE, subtype="PCM_24")
    path = build_set(tmp_path, [clip_xml(0, 4, "loop.wav", warped=True, markers=((0, 0), (1, 0.6)))])
    _, prepared, (region,) = regions_of(path, tmp_path)
    assert prepared.rendered == 1
    assert region.file != tmp_path / "loop.wav" and region.offset == 0
    assert region.length == pytest.approx(2.0)
    audio, rate = sf.read(region.file, always_2d=True)
    assert rate == RATE and len(audio) == 2 * RATE
    assert click_times(audio) == pytest.approx([0, 0.5, 1.0, 1.5], abs=0.006)


def test_warp_markers_inside_clip(tmp_path):
    # El primer tiempo dura 1 s en el archivo y el siguiente 0,25 s: dos velocidades distintas.
    audio = np.zeros((2 * RATE, 2), dtype=np.float32)
    for t in (0.0, 0.5, 1.0, 1.125):
        audio[int(t * RATE) : int(t * RATE) + 48] = 0.9
    sf.write(tmp_path / "w.wav", audio, RATE)
    path = build_set(
        tmp_path, [clip_xml(0, 2, "w.wav", warped=True, markers=((0, 0), (1, 1), (2, 1.25)))]
    )
    _, _, (region,) = regions_of(path, tmp_path)
    rendered, _ = sf.read(region.file, always_2d=True)
    # Al comprimir, Rubber Band suaviza los golpes cortos: umbral más bajo.
    assert click_times(rendered, threshold=0.1) == pytest.approx([0, 0.25, 0.5, 0.75], abs=0.006)


def test_unwarped_loop_is_unrolled_without_rendering(tmp_path):
    sf.write(tmp_path / "a.wav", clicks(2, 0.5), RATE)
    path = build_set(tmp_path, [clip_xml(0, 4, "a.wav", loop=(0.25, 0.75), content_start=0.25)])
    _, prepared, regions = regions_of(path, tmp_path)
    assert prepared.rendered == 0
    assert [(round(r.start, 6), round(r.length, 6), r.offset) for r in regions] == [
        (0.0, 0.5, 0.25), (0.5, 0.5, 0.25), (1.0, 0.5, 0.25), (1.5, 0.5, 0.25),
    ]


def test_warped_loop_is_unrolled(tmp_path):
    sf.write(tmp_path / "a.wav", clicks(4, 0.5), RATE)
    path = build_set(
        tmp_path, [clip_xml(8, 16, "a.wav", warped=True, loop=(0, 4), content_start=2)]
    )
    _, prepared, regions = regions_of(path, tmp_path)
    assert prepared.rendered == 0
    # Empieza en el tiempo 2 del bucle: media vuelta y luego vueltas completas.
    assert [(r.start, r.length, r.offset) for r in regions] == pytest.approx(
        [(4.0, 1.0, 1.0), (5.0, 2.0, 0.0), (7.0, 1.0, 0.0)]
    )


def test_transposed_clip_keeps_length_and_changes_pitch(tmp_path):
    sf.write(tmp_path / "t.wav", tone(2, 440), RATE)
    path = build_set(tmp_path, [clip_xml(0, 4, "t.wav", warped=True, transpose=12)])
    _, prepared, (region,) = regions_of(path, tmp_path)
    assert prepared.rendered == 1
    audio, _ = sf.read(region.file, always_2d=True)
    assert len(audio) == 2 * RATE
    assert peak_frequency(audio[RATE // 2 : RATE + RATE // 2]) == pytest.approx(880, abs=2)


def test_repitch_changes_speed_and_pitch(tmp_path):
    # Material a 100 BPM en una sesión a 120 BPM, modo Re-Pitch: suena un 20 % más agudo.
    sf.write(tmp_path / "t.wav", tone(3, 440), RATE)
    path = build_set(
        tmp_path, [clip_xml(0, 4, "t.wav", warped=True, markers=((0, 0), (1, 0.6)), warp_mode=3)]
    )
    _, prepared, (region,) = regions_of(path, tmp_path)
    audio, _ = sf.read(region.file, always_2d=True)
    assert len(audio) == 2 * RATE
    assert peak_frequency(audio[RATE // 2 : RATE + RATE // 2]) == pytest.approx(528, abs=2)


def test_unwarped_transpose_changes_speed(tmp_path):
    # Sin warp, transponer una octava reproduce el archivo al doble de velocidad.
    sf.write(tmp_path / "a.wav", clicks(4, 0.5), RATE)
    path = build_set(tmp_path, [clip_xml(0, 2, "a.wav", transpose=12)])
    _, prepared, (region,) = regions_of(path, tmp_path)
    audio, _ = sf.read(region.file, always_2d=True)
    assert len(audio) == RATE
    assert click_times(audio) == pytest.approx([0, 0.25, 0.5, 0.75], abs=0.002)


def test_other_formats_and_sample_rates_are_converted(tmp_path):
    sf.write(tmp_path / "main.wav", clicks(2, 0.5), RATE)
    sf.write(tmp_path / "other.flac", clicks(2, 0.5, rate=44100), 44100)
    path = build_set(
        tmp_path, [clip_xml(0, 2, "main.wav"), clip_xml(4, 6, "other.flac")]
    )
    _, prepared, regions = regions_of(path, tmp_path)
    assert prepared.rate == RATE
    converted = regions[1].file
    assert converted.suffix == ".wav" and converted.parent == tmp_path / "work"
    info = sf.info(converted)
    assert (info.samplerate, info.subtype) == (RATE, "PCM_24")
    audio, _ = sf.read(converted, always_2d=True)
    assert click_times(audio) == pytest.approx([0, 0.5, 1.0, 1.5], abs=0.0015)


def test_disabled_clips_are_skipped(tmp_path):
    sf.write(tmp_path / "a.wav", clicks(2, 0.5), RATE)
    path = build_set(tmp_path, [clip_xml(0, 2, "a.wav", disabled=True), clip_xml(4, 6, "a.wav")])
    project = ableton.read(path)
    assert len(project.tracks[0].audio_clips) == 1
    assert any("desactivado" in w for w in project.warnings)


def test_aaf_contains_rendered_audio_and_clip_gain(tmp_path):
    sf.write(tmp_path / "loop.wav", clicks(5, 0.6), RATE, subtype="PCM_24")
    path = build_set(
        tmp_path, [clip_xml(0, 4, "loop.wav", warped=True, markers=((0, 0), (1, 0.6)), gain=0.5)]
    )
    convert(path, tmp_path / "out", targets=("aaf",))
    with aaf2.open(str(tmp_path / "out" / "Set.aaf"), "r") as f:
        (composition,) = list(f.content.toplevel())
        (slot,) = list(composition.slots)
        (group,) = list(slot.segment.components)
        assert group.operation.name == "Mono Audio Gain"
        assert float(next(iter(group.parameters)).value) == pytest.approx(0.5)
        (clip,) = list(group.segments)
        assert (clip.start, clip.length) == (0, 2 * RATE)
        (master,) = list(f.content.mastermobs())
        assert master.name == "Audio - clip"
