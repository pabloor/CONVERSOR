"""Marcadores, dispositivos, envolventes MIDI, fundidos y opciones de exportación."""

import gzip

import aaf2
import mido
import numpy as np
import pytest
import soundfile as sf

from conversor.convert import convert, convert_many
from conversor.formats import ableton

RATE = 48000

SET = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12049">
 <LiveSet>
  <Tracks>
   <GroupTrack Id="1">
    <Name><EffectiveName Value="Banda"/><UserName Value=""/></Name>
    <TrackGroupId Value="-1"/>
    <DeviceChain><Mixer><Volume><Manual Value="1"/></Volume></Mixer></DeviceChain>
   </GroupTrack>
   <GroupTrack Id="2">
    <Name><EffectiveName Value="Ritmo"/><UserName Value=""/></Name>
    <TrackGroupId Value="1"/>
    <DeviceChain><Mixer><Volume><Manual Value="1"/></Volume></Mixer></DeviceChain>
   </GroupTrack>
   <AudioTrack Id="3">
    <Name><EffectiveName Value="Caja"/><UserName Value=""/></Name>
    <TrackGroupId Value="2"/>
    <DeviceChain>
     <Mixer><Volume><Manual Value="1"/></Volume><Pan><Manual Value="0"/></Pan><Speaker><Manual Value="true"/></Speaker></Mixer>
     <MainSequencer><Sample><ArrangerAutomation><Events>
      <AudioClip Id="0" Time="0">
       <CurrentStart Value="0"/><CurrentEnd Value="4"/>
       <Loop><LoopStart Value="0"/><LoopEnd Value="8"/><StartRelative Value="0"/><LoopOn Value="false"/></Loop>
       <Name Value="caja"/>
       <SampleRef><FileRef><RelativePath Value="tono.wav"/></FileRef></SampleRef>
       <WarpMarkers><WarpMarker Id="0" BeatTime="0" SecTime="0"/><WarpMarker Id="1" BeatTime="2" SecTime="1"/></WarpMarkers>
       <IsWarped Value="true"/>
       <Fades><FadeInLength Value="1"/><FadeOutLength Value="0.5"/></Fades>
      </AudioClip>
     </Events></ArrangerAutomation></Sample></MainSequencer>
     <DeviceChain><Devices>
      <Eq8 Id="0"><On><Manual Value="false"/></On><UserName Value=""/></Eq8>
      <AudioEffectGroupDevice Id="1"><On><Manual Value="true"/></On><UserName Value="Mi cadena"/></AudioEffectGroupDevice>
     </Devices></DeviceChain>
    </DeviceChain>
   </AudioTrack>
   <MidiTrack Id="4">
    <Name><EffectiveName Value="Synth"/><UserName Value=""/></Name>
    <TrackGroupId Value="-1"/>
    <DeviceChain>
     <Mixer><Volume><Manual Value="1"/></Volume><Pan><Manual Value="0"/></Pan><Speaker><Manual Value="true"/></Speaker></Mixer>
     <MainSequencer>
      <ClipTimeable><ArrangerAutomation><Events>
       <MidiClip Id="0" Time="0">
        <CurrentStart Value="0"/><CurrentEnd Value="8"/>
        <Loop><LoopStart Value="0"/><LoopEnd Value="4"/><StartRelative Value="0"/><LoopOn Value="true"/></Loop>
        <Name Value="frase"/>
        <Envelopes><Envelopes>
         <ClipEnvelope Id="0">
          <EnvelopeTarget><PointeeId Value="500"/></EnvelopeTarget>
          <Automation><Events>
           <FloatEvent Id="0" Time="-63072000" Value="0"/>
           <FloatEvent Id="1" Time="2" Value="0"/>
           <FloatEvent Id="2" Time="2" Value="4096"/>
          </Events></Automation>
         </ClipEnvelope>
         <ClipEnvelope Id="1">
          <EnvelopeTarget><PointeeId Value="503"/></EnvelopeTarget>
          <Automation><Events>
           <FloatEvent Id="0" Time="0" Value="0"/>
           <FloatEvent Id="1" Time="1" Value="127"/>
          </Events></Automation>
         </ClipEnvelope>
        </Envelopes></Envelopes>
        <Notes><KeyTracks><KeyTrack Id="0">
         <Notes>
          <MidiNoteEvent Time="0" Duration="1" Velocity="100" IsEnabled="true"/>
          <MidiNoteEvent Time="2" Duration="1" Velocity="100" Probability="0.5" IsEnabled="true"/>
         </Notes>
         <MidiKey Value="60"/>
        </KeyTrack></KeyTracks></Notes>
       </MidiClip>
      </Events></ArrangerAutomation></ClipTimeable>
      <MidiControllers>
       <ControllerTargets.0 Id="500"/>
       <ControllerTargets.1 Id="501"/>
       <ControllerTargets.3 Id="503"/>
      </MidiControllers>
     </MainSequencer>
     <DeviceChain><Devices>
      <PluginDevice Id="0">
       <On><Manual Value="true"/></On><UserName Value=""/>
       <PluginDesc><Vst3PluginInfo Id="0"><Name Value="Serum"/></Vst3PluginInfo></PluginDesc>
      </PluginDevice>
      <AuPluginDevice Id="1">
       <On><Manual Value="true"/></On><UserName Value=""/>
       <PluginDesc><AuPluginInfo Id="0"><Name Value="ChromaVerb"/></AuPluginInfo></PluginDesc>
      </AuPluginDevice>
     </Devices></DeviceChain>
    </DeviceChain>
   </MidiTrack>
   <AudioTrack Id="5">
    <Name><EffectiveName Value="Toma vieja"/><UserName Value=""/></Name>
    <TrackGroupId Value="-1"/>
    <DeviceChain>
     <Mixer><Volume><Manual Value="1"/></Volume><Pan><Manual Value="0"/></Pan><Speaker><Manual Value="false"/></Speaker></Mixer>
     <MainSequencer><Sample><ArrangerAutomation><Events>
      <AudioClip Id="0" Time="0">
       <CurrentStart Value="0"/><CurrentEnd Value="2"/>
       <Loop><LoopStart Value="0"/><LoopEnd Value="8"/><StartRelative Value="0"/><LoopOn Value="false"/></Loop>
       <Name Value="vieja"/>
       <SampleRef><FileRef><RelativePath Value="tono.wav"/></FileRef></SampleRef>
       <WarpMarkers><WarpMarker Id="0" BeatTime="0" SecTime="0"/><WarpMarker Id="1" BeatTime="2" SecTime="1"/></WarpMarkers>
       <IsWarped Value="true"/>
      </AudioClip>
     </Events></ArrangerAutomation></Sample></MainSequencer>
    </DeviceChain>
   </AudioTrack>
  </Tracks>
  <MainTrack>
   <DeviceChain>
    <Mixer>
     <Tempo><Manual Value="120"/><AutomationTarget Id="8"/></Tempo>
     <TimeSignature><Manual Value="201"/><AutomationTarget Id="9"/></TimeSignature>
    </Mixer>
    <DeviceChain><Devices><Limiter Id="0"><On><Manual Value="true"/></On><UserName Value=""/></Limiter></Devices></DeviceChain>
   </DeviceChain>
  </MainTrack>
  <Locators><Locators>
   <Locator Id="1"><Time Value="8"/><Name Value="Estribillo 🎶"/></Locator>
   <Locator Id="0"><Time Value="0"/><Name Value="Intro"/></Locator>
  </Locators></Locators>
 </LiveSet>
</Ableton>
"""


@pytest.fixture
def session(tmp_path):
    folder = tmp_path / "Sesion"
    folder.mkdir()
    t = np.arange(4 * RATE) / RATE
    sf.write(folder / "tono.wav", (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), RATE)
    path = folder / "Detalles.als"
    path.write_bytes(gzip.compress(SET.encode()))
    return path


def test_reads_markers_and_devices(session):
    project = ableton.read(session)
    assert [(m.beat, m.name) for m in project.markers] == [(0, "Intro"), (8, "Estribillo 🎶")]
    tracks = {t.name: t for t in project.tracks}
    assert tracks["Caja"].devices == ["EQ Eight (desactivado)", "Mi cadena [Audio Effect Rack]"]
    assert tracks["Synth"].devices == ["Serum (VST3)", "ChromaVerb (AU)"]
    assert project.master_devices == ["Limiter"]


def test_reads_fades_and_clip_envelopes(session):
    project = ableton.read(session)
    tracks = {t.name: t for t in project.tracks}
    (caja,) = tracks["Caja"].audio_clips
    assert (caja.fade_in, caja.fade_out) == (1, 0.5)

    (frase,) = tracks["Synth"].midi_clips
    bends = [(e.beat, e.value) for e in frase.controllers if e.kind == "pitchbend"]
    # El salto del tiempo 2 se repite en la segunda vuelta del bucle (tiempo 6).
    assert bends == [(0, 0), (2, 4096), (4, 0), (6, 4096)]
    modulation = [(e.beat, e.value) for e in frase.controllers if e.kind == "cc" and e.number == 1]
    assert modulation[0] == (0, 0) and (1, 127) in modulation
    assert any(0 < v < 127 for _, v in modulation)  # rampa entre 0 y 127
    assert any("probabilidad" in w for w in project.warnings)


def test_midi_has_markers_and_controllers(session, tmp_path):
    convert(session, tmp_path, targets=("midi",))
    midi = mido.MidiFile(str(tmp_path / "Detalles.mid"))
    conductor, synth = midi.tracks
    markers = [m.text for m in conductor if m.type == "marker"]
    assert markers == ["Intro", "Estribillo ?"]
    assert any(m.type == "pitchwheel" and m.pitch == 4096 for m in synth)
    assert any(m.type == "control_change" and m.control == 1 and m.value == 127 for m in synth)


def test_fades_in_stems_and_aaf(session, tmp_path):
    convert(session, tmp_path, targets=("aaf", "stems"), skip_muted=True)
    stem = tmp_path / "Detalles - pistas" / "01 Banda - Ritmo - Caja.wav"
    audio, _ = sf.read(stem, always_2d=True)
    level = lambda a, b: np.abs(audio[int(a * RATE) : int(b * RATE), 0]).max()  # noqa: E731
    # Fundido de entrada de 1 tiempo (0,5 s) y de salida de medio tiempo (0,25 s).
    assert level(0, 0.05) < 0.06 and level(0.45, 0.5) > 0.4 and level(1.0, 1.5) > 0.49
    assert level(1.99, 2.0) < 0.03

    with aaf2.open(str(tmp_path / "Detalles.aaf"), "r") as f:
        (composition,) = list(f.content.toplevel())
        _, slot = list(composition.slots)  # la pista silenciada no se exporta
        (clip,) = list(slot.segment.components)
        assert clip["FadeInLength"].value == RATE // 2
        assert clip["FadeOutLength"].value == RATE // 4


def test_report_lists_devices_and_skipped_tracks(session, tmp_path):
    convert(session, tmp_path, skip_muted=True)
    report = (tmp_path / "Detalles - informe.txt").read_text(encoding="utf-8")
    assert "Banda - Ritmo - Caja: EQ Eight (desactivado) → Mi cadena [Audio Effect Rack]" in report
    assert "Synth: Serum (VST3) → ChromaVerb (AU)" in report
    assert "Master: Limiter" in report
    assert "No se exportan 1 pista silenciada: Toma vieja" in report
    assert "Marcadores: 2" in report


def test_without_group_prefix_and_with_muted(session, tmp_path):
    result = convert(session, tmp_path, targets=("stems",), group_prefix=False)
    names = sorted(p.name for p in (tmp_path / "Detalles - pistas").iterdir())
    assert names == ["01 Caja.wav", "02 Toma vieja.wav"]
    assert any("silenciada" in w for w in result.project.warnings)


def test_sample_rate_option(session, tmp_path):
    convert(session, tmp_path, targets=("stems",), sample_rate=44100)
    for stem in (tmp_path / "Detalles - pistas").iterdir():
        assert sf.info(str(stem)).samplerate == 44100


def test_progress_is_reported(session, tmp_path):
    seen = []
    convert(session, tmp_path, progress=lambda fraction, text: seen.append((fraction, text)))
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert any("Audio:" in text for _, text in seen)


def test_convert_many(session, tmp_path):
    other = session.with_name("Otra.als")
    other.write_bytes(session.read_bytes())
    broken = session.with_name("Rota.als")
    broken.write_bytes(b"no es un set")
    results = convert_many([session, other, broken], targets=("midi",))
    assert [p.name for p, _ in results] == ["Detalles.als", "Otra.als", "Rota.als"]
    assert (session.parent / "Detalles (convertido)" / "Detalles.mid").exists()
    assert (session.parent / "Otra (convertido)" / "Otra.mid").exists()
    assert isinstance(results[2][1], Exception)
