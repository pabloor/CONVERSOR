import aaf2
import mido

from conversor.cli import main
from conversor.convert import convert


def test_convert_generates_aaf_midi_and_report(als_session, tmp_path):
    out = tmp_path / "out"
    result = convert(als_session, out)
    assert sorted(p.name for p in result.outputs) == [
        "Demo - informe.txt", "Demo - pistas", "Demo.aaf", "Demo.mid",
    ]
    report = (out / "Demo - informe.txt").read_text(encoding="utf-8")
    assert "Avisos:" in report
    assert "Cómo abrirlo en Logic Pro" in report
    # Tabla de mezcla: volumen y panorama de cada pista.
    # El nombre del grupo va delante del de la pista.
    assert "Batería - Bombo  audio   -6.0 dB   50L  con automatización" in report
    assert "Bajo             MIDI    +0.0 dB     C  silenciada" in report


def test_stems_start_at_session_start(als_session, tmp_path):
    import numpy as np
    import soundfile as sf

    convert(als_session, tmp_path, targets=("stems",))
    (stem,) = (tmp_path / "Demo - pistas").iterdir()
    assert stem.name == "01 Batería - Bombo.wav"
    audio, rate = sf.read(stem, always_2d=True)
    assert rate == 48000 and len(audio) == 7 * rate
    level = np.abs(audio[:, 0]) > 0.01
    # Clips de 2 a 4 s y de 6 a 7 s; silencio en el resto.
    assert not level[: 2 * rate].any() and level[2 * rate : 4 * rate].all()
    assert not level[4 * rate : 6 * rate].any() and level[6 * rate :].all()


def test_aaf_places_clips_on_timeline(als_session, tmp_path):
    convert(als_session, tmp_path, targets=("aaf",))
    with aaf2.open(str(tmp_path / "Demo.aaf"), "r") as f:
        (composition,) = list(f.content.toplevel())
        timecode, slot = list(composition.slots)
        # Empieza en 01:00:00:00, el inicio por defecto de Logic y Pro Tools.
        assert (timecode.segment.fps, timecode.segment.start) == (25, 90000)
        assert slot.name == "Batería - Bombo"
        assert slot.edit_rate == 48000
        # Panorama y volumen de la pista envuelven la secuencia de clips.
        pan = slot.segment
        assert pan.operation.name == "Mono Audio Pan"
        assert float(next(iter(pan.parameters)).value) == 0.25
        gain = pan.segments[0]
        assert gain.operation.name == "Mono Audio Gain"
        points = [(float(p.time), round(float(p.value), 3)) for p in next(iter(gain.parameters)).pointlist]
        # Automatización: 0 dB al principio y -6 dB en el tiempo 8 (4 s)
        assert points[0][1] == 1.0 and points[-1][1] == 0.5
        assert points[-1][0] * gain.length == 4 * 48000

        components = list(gain.segments[0].components)
        kinds = [type(c).__name__ for c in components]
        assert kinds[:4] == ["Filler", "SourceClip", "Filler", "SourceClip"]
        # 120 BPM: tiempo 4 = 2 s, longitud 4 tiempos = 2 s, desplazamiento 0,5 s
        assert components[0].length == 96000
        assert (components[1].start, components[1].length) == (24000, 96000)
        assert components[2].length == 96000  # de 4 s a 6 s
        # Segundo clip: 1 s de duración, empieza en 0,5 s del archivo
        assert (components[3].start, components[3].length) == (24000, 48000)
        assert len(list(f.content.mastermobs())) == 1


def test_aaf_mixer_round_trip(als_session, tmp_path):
    from conversor.formats import aaf

    convert(als_session, tmp_path, targets=("aaf",))
    (drums,) = aaf.read(tmp_path / "Demo.aaf").tracks
    assert drums.pan == -0.5
    (volume,) = drums.automation
    assert [(round(p.beat, 3), round(p.value, 2)) for p in volume.points] == [(0, 0.0), (8, -6.02)]


def test_midi_contains_tempo_and_unrolled_notes(als_session, tmp_path):
    convert(als_session, tmp_path, targets=("midi",))
    midi = mido.MidiFile(str(tmp_path / "Demo.mid"))
    conductor, bass = midi.tracks
    # Sin CC7: Logic interpreta el volumen MIDI con otra curva.
    assert not any(m.type == "control_change" and m.control == 7 for m in bass)
    tempos = [mido.tempo2bpm(m.tempo) for m in conductor if m.type == "set_tempo"]
    assert [round(t) for t in tempos] == [120, 140]
    assert bass.name == "Bajo"

    now, starts = 0, []
    for message in bass:
        now += message.time
        if message.type == "note_on":
            starts.append((now / midi.ticks_per_beat, message.note))
    assert starts == [(0, 60), (2, 64), (4, 60), (6, 64)]


def test_cli_reports_errors(tmp_path, capsys):
    bad = tmp_path / "x.ptx"
    bad.write_text("")
    assert main([str(bad)]) == 1
    assert "Formato no compatible" in capsys.readouterr().err


def test_cli_converts(als_session, tmp_path, capsys):
    assert main([str(als_session), "-o", str(tmp_path / "cli")]) == 0
    assert "Bombo" in capsys.readouterr().out
