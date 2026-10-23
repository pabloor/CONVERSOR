import math

import pytest

from conversor.formats import ableton
from conversor.model import TrackKind


def test_reads_tempo_and_time_signature(als_session):
    project = ableton.read(als_session)
    assert [(p.beat, p.bpm) for p in project.tempo_map] == [(0.0, 120.0), (16.0, 140.0)]
    assert not any(p.ramp for p in project.tempo_map)
    sig = project.time_signatures[0]
    assert (sig.numerator, sig.denominator) == (4, 4)
    assert project.beats_to_seconds(20) == pytest.approx(8 + 4 * 60 / 140)


def test_reads_tracks_and_mixer(als_session):
    project = ableton.read(als_session)
    names = [t.name for t in project.tracks]
    assert names == ["Bombo", "Bajo", "Batería"]
    drums, bass, group = project.tracks
    assert drums.kind == TrackKind.AUDIO
    assert drums.volume_db == pytest.approx(-6.0, abs=0.01)
    assert drums.pan == -0.5
    assert drums.group == "Batería"
    assert bass.kind == TrackKind.MIDI and bass.mute
    assert group.kind == TrackKind.GROUP


def test_reads_volume_automation(als_session):
    drums = ableton.read(als_session).tracks[0]
    (volume,) = drums.automation
    assert volume.parameter == "volume"
    assert [(p.beat, round(p.value, 2)) for p in volume.points] == [(0.0, 0.0), (8.0, -6.02)]


def test_reads_audio_clips(als_session):
    drums = ableton.read(als_session).tracks[0]
    unwarped, warped = drums.audio_clips
    assert (unwarped.start, unwarped.length) == (4, 4)
    assert unwarped.source_offset == 0.5
    assert unwarped.file.name == "kick.wav" and unwarped.file.exists()
    assert (warped.start, warped.length) == (12, 2)
    assert warped.source_offset == pytest.approx(0.5)


def test_unrolls_looped_midi_clip_and_ignores_session_clips(als_session):
    bass = ableton.read(als_session).tracks[1]
    (clip,) = bass.midi_clips
    notes = [(n.pitch, n.start, n.duration, n.velocity) for n in clip.notes]
    assert notes == [(60, 0, 1, 100), (64, 2, 2, 90), (60, 4, 1, 100), (64, 6, 2, 90)]


def test_warns_about_group_tracks(als_session):
    project = ableton.read(als_session)
    assert any("Batería" in w for w in project.warnings)
    assert not any("estirado" in w for w in project.warnings)


def test_tempo_ramp_duration():
    from conversor.model import Project, TempoPoint

    project = Project("x", tempo_map=[TempoPoint(0, 100, ramp=True), TempoPoint(4, 200)])
    expected = 4 * 60 * math.log(2) / 100
    assert project.beats_to_seconds(4) == pytest.approx(expected)
    assert project.beats_to_seconds(5) == pytest.approx(expected + 60 / 200)
    assert project.tempo_at(2) == pytest.approx(150)


def test_rejects_non_ableton_file(tmp_path):
    path = tmp_path / "x.als"
    path.write_text("<Otro/>")
    with pytest.raises(ValueError):
        ableton.read(path)
