import aaf2
import mido

from conversor.cli import main
from conversor.convert import convert


def test_convert_generates_aaf_midi_and_report(als_session, tmp_path):
    out = tmp_path / "out"
    result = convert(als_session, out)
    assert sorted(p.name for p in result.outputs) == ["Demo - informe.txt", "Demo.aaf", "Demo.mid"]
    assert "Avisos:" in (out / "Demo - informe.txt").read_text(encoding="utf-8")


def test_aaf_places_clips_on_timeline(als_session, tmp_path):
    convert(als_session, tmp_path, targets=("aaf",))
    with aaf2.open(str(tmp_path / "Demo.aaf"), "r") as f:
        (composition,) = list(f.content.toplevel())
        (slot,) = list(composition.slots)
        assert slot.name == "Bombo"
        assert slot.edit_rate == 48000
        components = list(slot.segment.components)
        kinds = [type(c).__name__ for c in components]
        assert kinds == ["Filler", "SourceClip", "Filler", "SourceClip"]
        # 120 BPM: tiempo 4 = 2 s, longitud 4 tiempos = 2 s, desplazamiento 0,5 s
        assert components[0].length == 96000
        assert (components[1].start, components[1].length) == (24000, 96000)
        assert components[2].length == 96000  # de 4 s a 6 s
        # Segundo clip: 1 s de duración, empieza en 0,5 s del archivo
        assert (components[3].start, components[3].length) == (24000, 48000)
        assert len(list(f.content.mastermobs())) == 1


def test_midi_contains_tempo_and_unrolled_notes(als_session, tmp_path):
    convert(als_session, tmp_path, targets=("midi",))
    midi = mido.MidiFile(str(tmp_path / "Demo.mid"))
    conductor, bass = midi.tracks
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
