"""Pistas renderizadas en Ableton (con efectos), colocadas en los clips de la sesión."""

import math

import aaf2
import mido
import numpy as np
import pytest
import soundfile as sf

from conversor import rendered
from conversor.convert import convert
from conversor.model import AudioClip, Project, Track, TrackKind
from test_session_details import RATE, session  # noqa: F401 - fixture


def tone(seconds, until=None, rate=RATE):
    """Tono hasta `until` segundos y silencio hasta `seconds`."""
    audio = np.zeros((int(seconds * rate), 2), dtype=np.float32)
    end = int((until if until is not None else seconds) * rate)
    t = np.arange(end) / rate
    audio[:end] = (0.3 * np.sin(2 * np.pi * 330 * t))[:, None]
    return audio


@pytest.fixture
def renders(session):
    """Lo que exporta Ableton con «Todas las pistas individuales»."""
    folder = session.parent / "Exportación"
    folder.mkdir()
    # Clip de 0 a 2 s con una cola de reverb que dura hasta 2,6 s.
    sf.write(folder / "Detalles Caja.wav", tone(4, until=2.6), RATE)
    sf.write(folder / "Detalles Synth.wav", tone(4), RATE)
    sf.write(folder / "Detalles Toma Vieja.wav", tone(4, until=1), RATE)
    sf.write(folder / "Detalles Banda.wav", tone(4), RATE)
    sf.write(folder / "Detalles Master.wav", tone(4), RATE)
    sf.write(folder / "Otra cosa.wav", tone(1), RATE)
    return folder


def test_matches_files_to_tracks(session, renders):
    from conversor.formats import ableton

    result = rendered.match(renders, ableton.read(session))
    assert {k: v.name for k, v in result.matched.items()} == {
        "Caja": "Detalles Caja.wav",
        "Synth": "Detalles Synth.wav",
        "Toma vieja": "Detalles Toma Vieja.wav",
        "Banda": "Detalles Banda.wav",
    }
    assert result.master.name == "Detalles Master.wav"
    assert [p.name for p in result.unmatched] == ["Otra cosa.wav"]


def test_longest_track_name_wins(tmp_path):
    project = Project("x", tracks=[Track("Bajo", TrackKind.AUDIO), Track("Bajo 2", TrackKind.AUDIO)])
    for name in ("Set Bajo 2.wav", "Set Bajo.wav"):
        sf.write(tmp_path / name, tone(0.1), RATE)
    result = rendered.match(tmp_path, project)
    assert {k: v.name for k, v in result.matched.items()} == {"Bajo": "Set Bajo.wav", "Bajo 2": "Set Bajo 2.wav"}


def test_aaf_has_processed_and_clean_tracks(session, renders, tmp_path):
    out = tmp_path / "out"
    result = convert(session, out, targets=("aaf", "midi"), rendered=renders)
    with aaf2.open(str(out / "Detalles.aaf"), "r") as f:
        (composition,) = list(f.content.toplevel())
        slots = list(composition.slots)[1:]
        names = [s.name for s in slots]
        assert names == [
            "Banda - Ritmo - Caja", "Synth (audio)", "Toma vieja",
            "Banda - Ritmo - Caja (limpio)", "Toma vieja (limpio)",
        ]
        caja = slots[0].segment
        (clip,) = [c for c in caja.components if type(c).__name__ == "SourceClip"]
        # El clip acaba en 2 s, pero se alarga con la cola hasta 2,6 s.
        assert clip.start == 0 and clip.length == pytest.approx(2.6 * RATE, abs=RATE * 0.011)
        assert clip.mob.name == "Detalles Caja"
        # La versión limpia lleva el volumen al mínimo.
        clean = slots[3].segment
        assert clean.operation.name == "Mono Audio Gain"
        assert float(next(iter(clean.parameters)).value) == 0

    # Las notas siguen en el MIDI, y ya no se avisa de que falta el sonido del instrumento.
    synth = mido.MidiFile(str(out / "Detalles.mid")).tracks[1]
    assert synth.name == "Synth" and any(m.type == "note_on" for m in synth)
    assert not any("sin el sonido de sus instrumentos" in w for w in result.project.warnings)

    references = sorted(p.name for p in (out / "Detalles - referencias").iterdir())
    assert references == ["Detalles Banda.wav", "Detalles Master.wav"]
    report = (out / "Detalles - informe.txt").read_text(encoding="utf-8")
    assert "Caja ← Detalles Caja.wav" in report
    assert "Sin pista correspondiente (no se usan): Otra cosa.wav" in report


def test_without_clean_tracks_and_skipping_muted(session, renders, tmp_path):
    convert(session, tmp_path, targets=("stems",), rendered=renders, keep_clean=False, skip_muted=True)
    names = sorted(p.name for p in (tmp_path / "Detalles - pistas").iterdir())
    assert names == ["01 Banda - Ritmo - Caja.wav", "02 Synth (audio).wav"]
    audio, _ = sf.read(tmp_path / "Detalles - pistas" / "01 Banda - Ritmo - Caja.wav", always_2d=True)
    # La pista procesada conserva la cola: suena hasta 2,6 s.
    assert np.abs(audio[int(2.5 * RATE) : int(2.55 * RATE)]).max() > 0.2


def test_returns_become_tracks(tmp_path):
    sf.write(tmp_path / "Set A-Reverb.wav", tone(3, until=2), RATE)
    project = Project("Set", tracks=[Track("A-Reverb", TrackKind.RETURN)])
    rendered.apply(project, rendered.match(tmp_path, project))
    (track,) = project.tracks
    assert track.kind == TrackKind.AUDIO and track.name == "A-Reverb"
    (clip,) = track.audio_clips
    assert (clip.start, clip.source_offset) == (0, 0) and clip.length == pytest.approx(4.0, abs=0.03)


def test_tail_stops_at_next_clip(tmp_path):
    sf.write(tmp_path / "Set Voz.wav", tone(4), RATE)  # suena todo el rato
    track = Track(
        "Voz", TrackKind.AUDIO,
        audio_clips=[AudioClip("a", 0, 2, None), AudioClip("b", 3, 2, None)],
    )
    project = Project("Set", tracks=[track])
    rendered.apply(project, rendered.match(tmp_path, project), keep_clean=False)
    first, second = project.tracks[0].audio_clips
    assert (first.start, first.length) == (0, pytest.approx(3))  # hasta el siguiente clip
    assert (second.start, second.source_offset) == (3, 1.5)


def test_render_start_bar(session, renders, tmp_path):
    from conversor.convert import _bar_to_beat
    from conversor.formats import ableton

    assert _bar_to_beat(ableton.read(session), 3) == 8
    project = ableton.read(session)
    result = rendered.match(renders, project)
    rendered.apply(project, result, keep_clean=False, start_beat=4)  # la exportación empieza en el tiempo 4 (2 s)
    synth = next(t for t in project.tracks if t.name == "Synth (audio)")
    (clip,) = synth.audio_clips
    # El clip MIDI empieza en el tiempo 0, antes de la exportación: el audio empieza en 2 s.
    assert clip.start == pytest.approx(4) and clip.source_offset == 0
    assert math.isclose(project.beats_to_seconds(clip.start), 2.0)
