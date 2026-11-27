import io
from pathlib import Path
import shutil
import subprocess
import wave

import pytest

from AssetbundleUtils import BundleProject as project, UnityPy_AOV
from AssetbundleUtils import ProjectMedia
from tests.test_project_unicode_export import make_streamed_bundle


def wav_payload(channels, rate, frames, sample_width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((channels, sample_width, rate, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x32" * (channels * sample_width * frames))
    return output.getvalue()


def make_project(tmp_path, kind, payload):
    source = make_streamed_bundle(tmp_path / "sample.assetbundle", kind, payload)
    exported = project.export_bundle_project(str(source), str(tmp_path / "projects"))
    root = Path(exported["path"])
    entry = next(x for x in project._load_manifest(str(root))["assets"] if x["type"] == kind)
    sidecar = root / entry["stream"]["payload"]
    assert sidecar.read_bytes() == payload
    return root, sidecar, tmp_path / "rebuilt.assetbundle"


def rebuild_clip(root, output, kind):
    report = project.rebuild_bundle_project(str(root), str(output))
    assert report["project_sync_status"] == "updated"
    clip = next(o for o in UnityPy_AOV.load(str(output)).objects if o.type.name == kind)
    assert clip.path_id == 222
    return clip.read(False)


def test_pcm_wav_change_updates_all_audio_metadata(tmp_path):
    prior = wav_payload(1, 8000, 800)
    new = wav_payload(2, 16000, 3456, sample_width=3)
    root, sidecar, output = make_project(tmp_path, "AudioClip", prior)
    sidecar.write_bytes(new)
    for _ in range(3):
        clip = rebuild_clip(root, output, "AudioClip")
        assert clip.m_AudioData == new and sidecar.read_bytes() == new
        assert (clip.m_Channels, clip.m_Frequency, clip.m_BitsPerSample) == (2, 16000, 24)
        assert clip.m_Length == pytest.approx(3456 / 16000)
        assert int(clip.m_CompressionFormat) == 0 and clip.m_PreloadAudioData
        with wave.open(io.BytesIO(clip.m_AudioData), "rb") as stream:
            assert (stream.getnchannels(), stream.getframerate()) == (2, 16000)
            assert stream.getsampwidth() == 3 and len(stream.readframes(3457)) == 3456 * 6


def test_opaque_stream_unchanged_but_malformed_replacement_is_not_published(tmp_path, monkeypatch):
    kind = "VideoClip"
    payload = b"Original opaque video bytes accepted for unmodified rebuild"
    root, sidecar, output = make_project(tmp_path, kind, payload)
    # No native tools required to rebuild unchanged clips.
    monkeypatch.setattr(ProjectMedia.shutil, "which", lambda _: None)
    assert rebuild_clip(root, output, kind).m_VideoData == payload
    old_output = output.read_bytes()
    manifest = root / "bundle_manifest.json"
    old_manifest = manifest.read_bytes()
    replacement = b"unverified"
    sidecar.write_bytes(replacement)
    with pytest.raises(ValueError, match=r"VideoClip PathID 222.*FFprobe.*donor AB"):
        project.rebuild_bundle_project(str(root), str(output))
    assert output.read_bytes() == old_output
    assert manifest.read_bytes() == old_manifest
    assert sidecar.read_bytes() == replacement


@pytest.mark.parametrize("bad", ["truncated", "arbitrary", "incomplete_pcm_frame"])
def test_unverifiable_audio_does_not_replace_output(tmp_path, bad):
    prior = wav_payload(1, 8000, 800)
    root, sidecar, output = make_project(tmp_path, "AudioClip", prior)
    assert rebuild_clip(root, output, "AudioClip").m_AudioData == prior
    old_output = output.read_bytes()
    manifest = root / "bundle_manifest.json"
    old_manifest = manifest.read_bytes()
    replacement = {
        "truncated": prior[:-4],
        "arbitrary": b"not an audio stream",
        "incomplete_pcm_frame": prior[:4] + (len(prior)-9).to_bytes(4, "little")
            + prior[8:40] + (1599).to_bytes(4, "little") + prior[44:-1],
    }[bad]
    sidecar.write_bytes(replacement)
    with pytest.raises(ValueError, match=r"AudioClip PathID 222.*donor AB"):
        project.rebuild_bundle_project(str(root), str(output))
    assert output.read_bytes() == old_output and sidecar.read_bytes() == replacement
    assert manifest.read_bytes() == old_manifest


def make_mp4(tmp_path, name, color, size="16x16", rate=24, frames=2):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("real MP4 coverage needs optional FFmpeg/FFprobe")
    output = tmp_path / name
    subprocess.run(
        [ffmpeg, "-loglevel", "error", "-threads", "1", "-f", "lavfi", "-i",
         f"color=c={color}:s={size}:r={rate}", "-frames:v", str(frames),
         "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(output)],
        check=True, timeout=15,
    )
    return output.read_bytes()


def test_verified_same_spec_mp4_rebuilds_without_changing_metadata(tmp_path):
    prior = make_mp4(tmp_path, "blue.mp4", "blue")
    new = make_mp4(tmp_path, "red.mp4", "red")
    assert new != prior
    root, sidecar, output = make_project(tmp_path, "VideoClip", prior)
    sidecar.write_bytes(new)
    for _ in range(3):
        clip = rebuild_clip(root, output, "VideoClip")
        assert clip.m_VideoData == new and sidecar.read_bytes() == new
        assert (clip.Width, clip.Height, clip.m_FrameRate, clip.m_FrameCount) == (16, 16, 24., 2)


def test_changed_mp4_specs_are_rejected_without_publishing(tmp_path):
    prior = make_mp4(tmp_path, "blue.mp4", "blue")
    new = make_mp4(tmp_path, "larger.mp4", "red", size="32x16", rate=30, frames=5)
    root, sidecar, output = make_project(tmp_path, "VideoClip", prior)
    assert rebuild_clip(root, output, "VideoClip").m_VideoData == prior
    old_output = output.read_bytes()
    manifest = root / "bundle_manifest.json"
    old_manifest = manifest.read_bytes()
    sidecar.write_bytes(new)
    with pytest.raises(ValueError, match="replacement MP4 changes.*donor AB"):
        project.rebuild_bundle_project(str(root), str(output))
    assert output.read_bytes() == old_output and sidecar.read_bytes() == new
    assert manifest.read_bytes() == old_manifest


def test_compatible_whole_media_donor_remains_rebuildable_without_ffprobe(tmp_path, monkeypatch):
    kind = "VideoClip"
    prior = b"old opaque test track compatible with donor layout"
    new = b"new opaque test track is compatible with target"
    root, _sidecar, output = make_project(tmp_path, kind, prior)
    donor = make_streamed_bundle(tmp_path / "donor.assetbundle", kind, new, path_id=333)
    monkeypatch.setattr(ProjectMedia.shutil, "which", lambda _: None)
    project.stage_cross_bundle_assets(str(root), {str(donor): [333]}, remove_path_ids=[222])
    for _ in range(2):
        report = project.rebuild_bundle_project(str(root), str(output))
        assert report["project_sync_status"] == "updated"
        clips = [o for o in UnityPy_AOV.load(str(output)).objects if o.type.name == kind]
        assert len(clips) == 1 and clips[0].path_id == 333
        assert clips[0].read(False).m_VideoData == new
