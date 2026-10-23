"""Conversión inversa: AAF (y MIDI) de Pro Tools a Ableton Live."""

import gzip
import struct
import uuid
import wave
import xml.etree.ElementTree as ET

import aaf2
import mido
import pytest

from conftest import SAMPLE_RATE, write_wav
from conversor.cli import main
from conversor.convert import convert, load
from conversor.formats import ableton


def _register_operation(f, name, parameter=None):
    operation = f.create.OperationDef(uuid.uuid4(), name, "")
    operation.media_kind = "sound"
    operation["IsTimeWarp"].value = False
    operation["NumberInputs"].value = -1
    f.dictionary.register_def(operation)
    if parameter:
        definition = f.create.ParameterDef(uuid.uuid4(), parameter, "", "Rational")
        f.dictionary.register_def(definition)
        operation["ParametersDefined"].append(definition)
    return operation


def _write_constant_wav(path, value, seconds=2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * SAMPLE_RATE)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(struct.pack(f"<{frames}h", *([value] * frames)))


@pytest.fixture
def pro_tools_export(tmp_path):
    """AAF al estilo de Pro Tools: pista estéreo con audio incrustado (un mono
    por canal) y pista mono enlazada a un archivo en «Audio Files», con
    automatización de volumen."""
    media = tmp_path / "media"
    _write_constant_wav(media / "Voz.L.wav", 1000)
    _write_constant_wav(media / "Voz.R.wav", -1000)
    export = tmp_path / "Export"
    linked = export / "Audio Files" / "Guitarra.wav"
    write_wav(linked, 3.0)

    path = export / "Cancion.aaf"
    with aaf2.open(str(path), "w") as f:
        combiner = _register_operation(f, "Audio Channel Combiner")
        gain = _register_operation(f, "Audio Gain", "Amplitude")
        composition = f.create.CompositionMob("Cancion")
        composition.usage = "Usage_TopLevel"
        f.content.mobs.append(composition)

        channels = []
        for channel in ("L", "R"):
            master = f.create.MasterMob(f"Voz.{channel}")
            f.content.mobs.append(master)
            master.import_audio_essence(str(media / f"Voz.{channel}.wav"), edit_rate=SAMPLE_RATE)
            channels.append(master)
        voice = composition.create_sound_slot(edit_rate=SAMPLE_RATE)
        voice.name = "Voz"
        group = f.create.OperationGroup(combiner, length=SAMPLE_RATE, media_kind="sound")
        for master in channels:
            group.segments.append(master.create_source_clip(slot_id=1, start=SAMPLE_RATE // 2, length=SAMPLE_RATE))
        voice.segment.components.append(f.create.Filler("sound", SAMPLE_RATE))
        voice.segment.components.append(group)

        guitar_master = f.create.MasterMob("Guitarra")
        f.content.mobs.append(guitar_master)
        guitar_master.import_audio_essence(str(linked), edit_rate=SAMPLE_RATE, offline=True)
        source_mob = next(m for m in f.content.sourcemobs() if m.name == "Guitarra.PHYS")
        locator = f.create.NetworkLocator()
        locator["URLString"].value = "file:///Volumes/Disco%20antiguo/Audio%20Files/Guitarra.wav"
        source_mob.descriptor["Locator"].append(locator)

        guitar = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        guitar.name = "Guitarra"
        sequence = f.create.Sequence("sound")
        sequence.components.append(f.create.Filler("sound", 2 * SAMPLE_RATE))
        sequence.components.append(guitar_master.create_source_clip(slot_id=1, start=0, length=2 * SAMPLE_RATE))
        volume = f.create.OperationGroup(gain, length=4 * SAMPLE_RATE, media_kind="sound")
        volume.segments.append(sequence)
        varying = f.create.VaryingValue()
        varying.parameterdef = f.dictionary.lookup_parameterdef("Amplitude")
        linear = f.create.InterpolationDef("5b6c85a4-0ede-11d3-80a9-006008143e6f", "LinearInterp", "")
        f.dictionary.register_def(linear)
        varying["Interpolation"].value = linear
        varying.add_keyframe(0, 1.0)
        varying.add_keyframe(0.5, 0.5)
        volume.parameters.append(varying)
        guitar.segment = volume

    midi = mido.MidiFile(ticks_per_beat=480)
    conductor = mido.MidiTrack(
        [
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(90), time=0),
            mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0),
        ]
    )
    keys = mido.MidiTrack(
        [
            mido.MetaMessage("track_name", name="Piano", time=0),
            mido.Message("note_on", note=60, velocity=80, time=480),
            mido.Message("note_off", note=60, velocity=0, time=240),
            mido.Message("note_on", note=67, velocity=90, time=1680),
            mido.Message("note_on", note=67, velocity=0, time=480),
        ]
    )
    midi.tracks.extend([conductor, keys])
    midi.save(str(export / "Cancion.mid"))
    return export


def test_reads_pro_tools_aaf_with_midi_tempo(pro_tools_export):
    project = load([pro_tools_export / "Cancion.aaf", pro_tools_export / "Cancion.mid"])
    assert [(p.beat, p.bpm) for p in project.tempo_map] == [(0.0, 90.0)]
    sig = project.time_signatures[0]
    assert (sig.numerator, sig.denominator) == (3, 4)

    tracks = {t.name: t for t in project.tracks}
    assert set(tracks) == {"Piano", "Voz", "Guitarra"}

    (voice,) = tracks["Voz"].audio_clips
    # 1 s a 90 BPM = 1,5 tiempos
    assert voice.start == pytest.approx(1.5)
    assert voice.length == pytest.approx(1.5)
    assert voice.source_offset == pytest.approx(0.5)
    assert voice.file in project.embedded_media

    (guitar,) = tracks["Guitarra"].audio_clips
    assert guitar.start == pytest.approx(3.0)
    assert guitar.file.name == "Guitarra.wav" and guitar.file.parent.name == "Audio Files"
    (volume,) = tracks["Guitarra"].automation
    assert [(round(p.beat, 3), round(p.value, 2)) for p in volume.points] == [(0, 0.0), (3, -6.02)]

    (piano_clip,) = tracks["Piano"].midi_clips
    assert piano_clip.start == 0
    assert [(n.pitch, piano_clip.start + n.start, n.duration) for n in piano_clip.notes] == [
        (60, 1.0, 0.5),
        (67, 5.0, 1.0),
    ]


def test_aaf_without_midi_uses_given_tempo(pro_tools_export):
    project = load([pro_tools_export / "Cancion.aaf"], tempo=60)
    voice = next(t for t in project.tracks if t.name == "Voz")
    assert voice.audio_clips[0].start == pytest.approx(1.0)
    assert not any("120 BPM" in w for w in project.warnings)
    assert any("120 BPM" in w for w in load([pro_tools_export / "Cancion.aaf"]).warnings)


def test_writes_ableton_set(pro_tools_export, tmp_path):
    out = tmp_path / "out"
    result = convert([pro_tools_export / "Cancion.aaf", pro_tools_export / "Cancion.mid"], out)
    als = out / "Cancion Project" / "Cancion.als"
    assert als in result.outputs

    back = ableton.read(als)
    assert back.tempo_map[0].bpm == 90
    assert (back.time_signatures[0].numerator, back.time_signatures[0].denominator) == (3, 4)
    tracks = {t.name: t for t in back.tracks}
    assert set(tracks) == {"Piano", "Voz", "Guitarra"}
    assert len(tracks["Piano"].midi_clips[0].notes) == 2

    voice = tracks["Voz"].audio_clips[0]
    assert (voice.start, voice.length, voice.source_offset) == pytest.approx((1.5, 1.5, 0.5))
    samples = out / "Cancion Project" / "Samples" / "Imported"
    assert voice.file == samples / "Voz.wav"
    with wave.open(str(voice.file), "rb") as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getnframes()) == (2, SAMPLE_RATE, 2 * SAMPLE_RATE)
        left, right = struct.unpack("<2h", wav.readframes(1))
        assert (left, right) == (1000, -1000)
    assert sorted(p.name for p in samples.iterdir()) == ["Guitarra.wav", "Voz.wav"]

    guitar = tracks["Guitarra"]
    assert guitar.audio_clips[0].file == samples / "Guitarra.wav"
    assert guitar.automation[0].parameter == "volume"


def test_ableton_set_has_unique_ids(pro_tools_export, tmp_path):
    convert([pro_tools_export / "Cancion.aaf", pro_tools_export / "Cancion.mid"], tmp_path)
    root = ET.fromstring(gzip.decompress((tmp_path / "Cancion Project" / "Cancion.als").read_bytes()))
    pointees = [
        int(e.get("Id"))
        for e in root.iter()
        if "Id" in e.attrib and (e.tag.endswith("Target") or e.tag == "Pointee")
    ]
    assert len(pointees) == len(set(pointees))
    assert int(root.find("LiveSet/NextPointeeId").get("Value")) > max(pointees)
    track_ids = [t.get("Id") for t in root.find("LiveSet/Tracks")]
    assert len(track_ids) == len(set(track_ids)) == 3
    for envelope in root.iter("AutomationEnvelope"):
        assert int(envelope.find("EnvelopeTarget/PointeeId").get("Value")) in pointees


def test_user_template_is_used(pro_tools_export, tmp_path):
    template = tmp_path / "Plantilla.als"
    convert([pro_tools_export / "Cancion.mid"], tmp_path / "t", ["ableton"])
    # Cualquier set con una pista de audio y otra MIDI sirve como plantilla.
    template.write_bytes((tmp_path / "t" / "Cancion Project" / "Cancion.als").read_bytes())
    with pytest.raises(ValueError, match="pista de audio y una pista MIDI"):
        convert([pro_tools_export / "Cancion.aaf"], tmp_path / "u", ["ableton"], template=template)

    root = ET.fromstring(gzip.decompress(template.read_bytes()))
    root.find("LiveSet").set("Marca", "plantilla del usuario")
    tracks = root.find("LiveSet/Tracks")
    builtin = ET.fromstring(
        gzip.decompress(convert([pro_tools_export / "Cancion.aaf"], tmp_path / "v", ["ableton"]).outputs[0].read_bytes())
    )
    tracks.insert(0, builtin.find("LiveSet/Tracks/AudioTrack"))
    template.write_bytes(gzip.compress(ET.tostring(root)))
    result = convert([pro_tools_export / "Cancion.aaf"], tmp_path / "w", ["ableton"], template=template)
    written = ET.fromstring(gzip.decompress(result.outputs[0].read_bytes()))
    assert written.find("LiveSet").get("Marca") == "plantilla del usuario"


def test_cli_reverse(pro_tools_export, tmp_path, capsys):
    code = main([str(pro_tools_export / "Cancion.aaf"), "--tempo", "100", "-o", str(tmp_path / "cli")])
    assert code == 0
    assert (tmp_path / "cli" / "Cancion Project" / "Cancion.als").exists()
    assert "Voz" in capsys.readouterr().out
