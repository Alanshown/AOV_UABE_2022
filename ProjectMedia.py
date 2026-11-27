"""Guard direct media-sidecar changes against stale Unity clip metadata."""
from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from AssetbundleUtils.UnityPy_AOV.enums import AudioCompressionFormat


def _pcm_wave_metadata(payload: bytes) -> tuple[int, int, int, float]:
    """Read one complete, packed RIFF PCM stream (not compressed WAV/FSB)."""
    if (len(payload) < 12 or payload[:4] != b"RIFF"
            or payload[8:12] != b"WAVE"
            or struct.unpack_from("<I", payload, 4)[0] + 8 != len(payload)):
        raise ValueError("the payload is not a complete RIFF PCM WAV")

    fmt = None
    data_size = None
    offset = 12
    while offset < len(payload):
        if len(payload) - offset < 8:
            raise ValueError("WAV chunk header is truncated")
        kind, size = struct.unpack_from("<4sI", payload, offset)
        start = offset + 8
        offset = start + size + size % 2
        # For a final odd-sized data chunk, some encoders omit the padding
        # byte (including Python's wave writer); its declared payload is intact.
        if offset > len(payload) and not (
            kind == b"data" and start + size == len(payload)
        ):
            raise ValueError("WAV chunk is truncated")
        if kind == b"fmt ":
            if fmt is not None or size < 16:
                raise ValueError("WAV format chunk is missing or ambiguous")
            fmt = struct.unpack_from("<HHIIHH", payload, start)
        elif kind == b"data":
            if data_size is not None:
                raise ValueError("WAV has more than one data chunk")
            data_size = size
    if fmt is None or not data_size:
        raise ValueError("WAV has no format or audio data")
    codec, channels, frequency, byte_rate, alignment, bits = fmt
    if (codec != 1 or not 1 <= channels <= 8 or not 0 < frequency <= 0x7FFFFFFF
            or bits not in (8, 16, 24, 32) or alignment != channels * (bits // 8)
            or byte_rate != frequency * alignment or data_size % alignment):
        raise ValueError("WAV must contain integral frames of packed PCM audio")
    return channels, frequency, bits, (data_size // alignment) / frequency


def update_audio_metadata(obj, clip, replacement: bytes) -> None:
    """Synchronize only the supported scalar fields, retaining every other byte."""
    if (tuple(obj.version) < (5,) or clip.m_CompressionFormat != AudioCompressionFormat.PCM
            or clip.m_IsTrackerFormat or clip.m_SubsoundIndex != 0):
        raise ValueError("only an existing single-stream PCM WAV AudioClip can be edited directly")
    _pcm_wave_metadata(clip.m_AudioData)
    new_values = _pcm_wave_metadata(replacement)
    endian = obj.reader.endian
    raw = bytes(obj.get_raw_data())
    # AudioClip's serialized layout begins with its aligned name (see
    # AudioClip.py), then LoadType before the four adjacent audio scalars.
    name = clip.m_Name.encode("utf-8", "surrogateescape")
    offset = 4 + ((4 + len(name) + 3) // 4 * 4)
    expected = struct.pack(endian + "iiif", clip.m_Channels, clip.m_Frequency,
                           clip.m_BitsPerSample, clip.m_Length)
    if raw[offset:offset + len(expected)] != expected:
        raise ValueError("cannot verify the serialized AudioClip metadata layout")
    updated = struct.pack(endian + "iiif", *new_values)
    if updated != expected:
        obj.set_raw_data(raw[:offset] + updated + raw[offset + len(expected):])


_VIDEO_ENTRIES = (
    "stream=codec_type,codec_name,codec_tag_string,profile,level,pix_fmt,"
    "width,height,coded_width,coded_height,sample_aspect_ratio,field_order,"
    "color_range,color_space,color_transfer,color_primaries,chroma_location,"
    "r_frame_rate,avg_frame_rate,nb_frames,nb_read_frames,duration_ts,time_base,"
    "extradata_hash:stream_disposition=attached_pic:stream_side_data=rotation:"
    "stream_tags=rotate"
)


def _inspect_mp4(payload: bytes, ffprobe: str) -> dict:
    # Deliberately constrain scope to ordinary MP4 with a standard initial
    # file-type box; avoid guessing a container/decoder from arbitrary bytes.
    if len(payload) < 16 or payload[4:8] != b"ftyp":
        raise ValueError("the video is not a supported MP4 stream")
    with tempfile.TemporaryDirectory(prefix="aov-media-") as temp:
        path = Path(temp, "clip.mp4")
        path.write_bytes(payload)
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-protocol_whitelist", "file",
                 "-enable_drefs", "0", "-use_absolute_path", "0", "-f", "mov",
                 "-threads", "1", "-count_frames", "-show_data_hash", "sha256",
                 "-show_entries", _VIDEO_ENTRIES, "-of", "json", "-i", str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("FFprobe could not finish verifying the MP4 stream") from exc
    if result.returncode or result.stderr.strip():
        raise ValueError("FFprobe could not decode every MP4 frame")
    try:
        streams = json.loads(result.stdout)["streams"]
        if len(streams) != 1:
            raise ValueError("only a single video track without audio is supported")
        stream = streams[0]
        if (stream["codec_type"] != "video"
                or stream["codec_tag_string"] not in ("avc1", "mp4v")
                or stream["disposition"]["attached_pic"]
                or stream.get("side_data_list") or stream.get("tags", {}).get("rotate")
                or int(stream["nb_frames"]) != int(stream["nb_read_frames"])
                or int(stream["nb_frames"]) <= 0 or int(stream["duration_ts"]) <= 0
                or not stream["extradata_hash"]):
            raise ValueError("MP4 codec, frame count or track metadata cannot be verified")
        return stream
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("FFprobe did not return a complete MP4 stream description") from exc


def verify_video_replacement(clip, replacement: bytes) -> None:
    """Allow only an ordinary, decoded MP4 with identical stream metadata."""
    if (clip.m_HasSplitAlpha or clip.m_AudioChannelCount or clip.m_AudioSampleRate
            or clip.m_AudioLanguage):
        raise ValueError("VideoClip with audio or split alpha must come from a compatible donor AB")
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise ValueError("install FFprobe to verify a same-spec MP4 replacement")
    before = _inspect_mp4(clip.m_VideoData, ffprobe)
    after = _inspect_mp4(replacement, ffprobe)
    try:
        fps = Fraction(before["r_frame_rate"])
        average_fps = Fraction(before["avg_frame_rate"])
        sar = Fraction(before["sample_aspect_ratio"].replace(":", "/"))
        unity_sar = Fraction(
            getattr(clip, "m_PixelAspecRatioNum", 1),
            getattr(clip, "m_PixelAspecRatioDen", 1),
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("MP4 frame rate or pixel aspect ratio cannot be verified") from exc
    if (before["width"] != clip.Width or before["height"] != clip.Height
            or int(before["nb_frames"]) != clip.m_FrameCount or fps <= 0
            or fps != average_fps or sar != unity_sar
            or not math.isclose(float(fps), clip.m_FrameRate, rel_tol=1e-7)):
        raise ValueError("original MP4 frame metadata does not match its VideoClip")
    if before != after:
        raise ValueError(
            "replacement MP4 changes the codec, dimensions, frame timing/count or other track metadata"
        )
