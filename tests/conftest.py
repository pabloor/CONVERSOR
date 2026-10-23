import gzip
import struct
import wave
from pathlib import Path

import pytest

SAMPLE_RATE = 48000

ALS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="11.0_433" Creator="Ableton Live 11.3">
 <LiveSet>
  <Tracks>
   <AudioTrack Id="10">
    <Name><EffectiveName Value="Bombo"/><UserName Value=""/></Name>
    <TrackGroupId Value="12"/>
    <AutomationEnvelopes><Envelopes>
     <AutomationEnvelope Id="0">
      <EnvelopeTarget><PointeeId Value="101"/></EnvelopeTarget>
      <Automation><Events>
       <FloatEvent Id="1" Time="-63072000" Value="1"/>
       <FloatEvent Id="2" Time="8" Value="0.5"/>
      </Events></Automation>
     </AutomationEnvelope>
    </Envelopes></AutomationEnvelopes>
    <DeviceChain>
     <Mixer>
      <Volume><Manual Value="0.5011872336"/><AutomationTarget Id="101"/></Volume>
      <Pan><Manual Value="-0.5"/><AutomationTarget Id="102"/></Pan>
      <Speaker><Manual Value="true"/><AutomationTarget Id="103"/></Speaker>
      <SoloSink Value="false"/>
     </Mixer>
     <MainSequencer>
      <Sample><ArrangerAutomation><Events>
       <AudioClip Id="0" Time="4">
        <CurrentStart Value="4"/><CurrentEnd Value="8"/>
        <Loop><LoopStart Value="0.5"/><LoopEnd Value="2.5"/><StartRelative Value="0"/><LoopOn Value="false"/></Loop>
        <Name Value="kick"/>
        <SampleRef><FileRef>
         <RelativePath Value="Samples/Imported/kick.wav"/>
         <Path Value="/no/existe/kick.wav"/>
        </FileRef></SampleRef>
        <IsWarped Value="false"/>
        <SampleVolume Value="1"/>
       </AudioClip>
       <AudioClip Id="1" Time="12">
        <CurrentStart Value="12"/><CurrentEnd Value="14"/>
        <Loop><LoopStart Value="1"/><LoopEnd Value="4"/><StartRelative Value="0"/><LoopOn Value="false"/></Loop>
        <Name Value="kick warp"/>
        <SampleRef><FileRef><RelativePath Value="Samples/Imported/kick.wav"/></FileRef></SampleRef>
        <WarpMarkers>
         <WarpMarker Id="0" SecTime="0" BeatTime="0"/>
         <WarpMarker Id="1" SecTime="1" BeatTime="2"/>
        </WarpMarkers>
        <IsWarped Value="true"/>
       </AudioClip>
      </Events></ArrangerAutomation></Sample>
     </MainSequencer>
    </DeviceChain>
   </AudioTrack>
   <MidiTrack Id="11">
    <Name><EffectiveName Value="2-MIDI"/><UserName Value="Bajo"/></Name>
    <TrackGroupId Value="-1"/>
    <DeviceChain>
     <Mixer>
      <Volume><Manual Value="1"/><AutomationTarget Id="111"/></Volume>
      <Pan><Manual Value="0"/><AutomationTarget Id="112"/></Pan>
      <Speaker><Manual Value="false"/><AutomationTarget Id="113"/></Speaker>
     </Mixer>
     <MainSequencer>
      <ClipTimeable><ArrangerAutomation><Events>
       <MidiClip Id="0" Time="0">
        <CurrentStart Value="0"/><CurrentEnd Value="8"/>
        <Loop><LoopStart Value="0"/><LoopEnd Value="4"/><StartRelative Value="0"/><LoopOn Value="true"/></Loop>
        <Name Value="riff"/>
        <Notes><KeyTracks>
         <KeyTrack Id="0">
          <Notes><MidiNoteEvent Time="0" Duration="1" Velocity="100" IsEnabled="true"/></Notes>
          <MidiKey Value="60"/>
         </KeyTrack>
         <KeyTrack Id="1">
          <Notes>
           <MidiNoteEvent Time="2" Duration="3" Velocity="90" IsEnabled="true"/>
           <MidiNoteEvent Time="3" Duration="1" Velocity="90" IsEnabled="false"/>
          </Notes>
          <MidiKey Value="64"/>
         </KeyTrack>
        </KeyTracks></Notes>
       </MidiClip>
      </Events></ArrangerAutomation></ClipTimeable>
     </MainSequencer>
     <ClipSlotList>
      <MidiClip Id="9" Time="0"><CurrentStart Value="0"/><CurrentEnd Value="4"/></MidiClip>
     </ClipSlotList>
    </DeviceChain>
   </MidiTrack>
   <GroupTrack Id="12">
    <Name><EffectiveName Value="Batería"/><UserName Value=""/></Name>
    <DeviceChain><Mixer><Volume><Manual Value="1"/></Volume></Mixer></DeviceChain>
   </GroupTrack>
  </Tracks>
  <MasterTrack>
   <AutomationEnvelopes><Envelopes>
    <AutomationEnvelope Id="0">
     <EnvelopeTarget><PointeeId Value="8"/></EnvelopeTarget>
     <Automation><Events>
      <FloatEvent Id="1" Time="-63072000" Value="120"/>
      <FloatEvent Id="2" Time="16" Value="120"/>
      <FloatEvent Id="3" Time="16" Value="140"/>
     </Events></Automation>
    </AutomationEnvelope>
   </Envelopes></AutomationEnvelopes>
   <DeviceChain><Mixer>
    <Tempo><Manual Value="120"/><AutomationTarget Id="8"/></Tempo>
    <TimeSignature><Manual Value="201"/><AutomationTarget Id="9"/></TimeSignature>
   </Mixer></DeviceChain>
  </MasterTrack>
 </LiveSet>
</Ableton>
"""


def write_wav(path: Path, seconds: float, rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{frames}h", *([1000] * frames)))


@pytest.fixture
def als_session(tmp_path: Path) -> Path:
    project_dir = tmp_path / "Demo Project"
    write_wav(project_dir / "Samples" / "Imported" / "kick.wav", 3.0)
    path = project_dir / "Demo.als"
    path.write_bytes(gzip.compress(ALS_TEMPLATE.encode("utf-8")))
    return path
