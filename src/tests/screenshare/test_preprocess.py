from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from zoom_scribe.screenshare.preprocess import (
    FrameBundle,
    PreprocessConfig,
    PreprocessingError,
    ROIMetadata,
    build_frame_time_mapping,
    create_bundles,
    detect_roi,
    extract_frames_at_fps,
    gate_frames_by_ssim,
    preprocess_video,
)


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    """Create a deterministic synthetic MP4 screenshare clip."""

    np.random.seed(0)
    video_path = tmp_path / "synthetic.mp4"
    frame_size = (640, 480)
    fps = 10.0
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
    writer = cv2.VideoWriter(str(video_path), fourcc, fps, frame_size)
    if not writer.isOpened():
        fourcc = cv2.VideoWriter_fourcc(*"avc1")  # type: ignore[attr-defined]
        writer = cv2.VideoWriter(str(video_path), fourcc, fps, frame_size)
    if not writer.isOpened():
        pytest.skip("Video writer could not be initialized")

    font = cv2.FONT_HERSHEY_SIMPLEX
    for frame_idx in range(int(fps * 3)):
        frame = np.full((frame_size[1], frame_size[0], 3), 255, dtype=np.uint8)
        text = "Frame 1-10" if frame_idx < fps else "Frame 11-20"
        cv2.putText(frame, text, (50, 240), font, 1.0, (0, 0, 0), 2, cv2.LINE_AA)
        writer.write(frame)

    writer.release()
    return video_path


@pytest.fixture
def default_config() -> PreprocessConfig:
    return PreprocessConfig()


def test_preprocess_config_defaults() -> None:
    config = PreprocessConfig()
    assert config.target_fps == 6.0
    assert config.roi_detection_duration_sec == 10.0
    assert config.ssim_threshold == 0.005
    assert config.bundle_max_frames == 6
    assert config.bundle_max_time_gap_sec == 2.0


def test_preprocess_config_validation_negative_fps() -> None:
    with pytest.raises(ValueError):
        PreprocessConfig(target_fps=0)


def test_preprocess_config_validation_negative_threshold() -> None:
    with pytest.raises(ValueError):
        PreprocessConfig(ssim_threshold=-0.1)


def test_roi_metadata_validation() -> None:
    roi = ROIMetadata(x=10, y=20, width=100, height=200, confidence=0.7)
    assert roi.width == 100
    assert roi.confidence == 0.7


def test_roi_metadata_negative_dimensions() -> None:
    with pytest.raises(ValueError):
        ROIMetadata(x=0, y=0, width=0, height=5, confidence=0.5)


def test_roi_metadata_invalid_confidence() -> None:
    with pytest.raises(ValueError):
        ROIMetadata(x=0, y=0, width=10, height=5, confidence=1.5)


def test_frame_bundle_construction() -> None:
    roi = ROIMetadata(x=0, y=0, width=2, height=2, confidence=1.0)
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    bundle = FrameBundle(
        frames=(frame,),
        frame_indices=(0,),
        timestamps_sec=(0.0,),
        roi=roi,
    )
    assert bundle.frames[0].shape == (2, 2, 3)


def test_frame_bundle_duration_property() -> None:
    roi = ROIMetadata(x=0, y=0, width=2, height=2, confidence=1.0)
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    bundle = FrameBundle(
        frames=(frame, frame),
        frame_indices=(0, 1),
        timestamps_sec=(0.0, 1.5),
        roi=roi,
    )
    assert bundle.duration_sec == pytest.approx(1.5)


def test_frame_bundle_validation_mismatched_lengths() -> None:
    roi = ROIMetadata(x=0, y=0, width=2, height=2, confidence=1.0)
    with pytest.raises(ValueError):
        FrameBundle(
            frames=(np.zeros((2, 2, 3), dtype=np.uint8),),
            frame_indices=(0, 1),
            timestamps_sec=(0.0,),
            roi=roi,
        )


def test_detect_roi_success(synthetic_video: Path, default_config: PreprocessConfig) -> None:
    roi = detect_roi(synthetic_video, default_config)
    assert roi.width > 0 and roi.height > 0
    assert 0.0 <= roi.confidence <= 1.0


def test_detect_roi_missing_file(default_config: PreprocessConfig) -> None:
    missing_path = Path("/tmp/nonexistent.mp4")
    with pytest.raises(PreprocessingError):
        detect_roi(missing_path, default_config)


def test_detect_roi_invalid_video(tmp_path: Path, default_config: PreprocessConfig) -> None:
    invalid_path = tmp_path / "invalid.mp4"
    invalid_path.write_bytes(b"not a video")
    with pytest.raises(PreprocessingError):
        detect_roi(invalid_path, default_config)


def test_extract_frames_at_fps(synthetic_video: Path, default_config: PreprocessConfig) -> None:
    roi = detect_roi(synthetic_video, default_config)
    frames = extract_frames_at_fps(synthetic_video, default_config.target_fps, roi)
    capture = cv2.VideoCapture(str(synthetic_video))
    native_fps = capture.get(cv2.CAP_PROP_FPS)
    frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    capture.release()
    if native_fps <= 0:
        native_fps = 30.0
    skip = max(1, round(native_fps / default_config.target_fps))
    expected = int(frame_count // skip + (1 if frame_count % skip else 0))
    assert abs(len(frames) - expected) <= 1


def test_extract_frames_crops_to_roi(
    synthetic_video: Path, default_config: PreprocessConfig
) -> None:
    roi = detect_roi(synthetic_video, default_config)
    frames = extract_frames_at_fps(synthetic_video, default_config.target_fps, roi)
    for _, _, frame in frames:
        assert frame.shape[1] == roi.width
        assert frame.shape[0] == roi.height


def test_extract_frames_timestamps_monotonic(
    synthetic_video: Path, default_config: PreprocessConfig
) -> None:
    roi = detect_roi(synthetic_video, default_config)
    frames = extract_frames_at_fps(synthetic_video, default_config.target_fps, roi)
    timestamps = [timestamp for _, timestamp, _ in frames]
    assert timestamps == sorted(timestamps)


def test_gate_frames_by_ssim_keeps_first() -> None:
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    frames = [(0, 0.0, frame), (1, 0.1, frame)]
    gated = gate_frames_by_ssim(frames, threshold=0.1)
    assert gated[0][0] == 0


def test_gate_frames_by_ssim_detects_change() -> None:
    base = np.zeros((10, 10, 3), dtype=np.uint8)
    changed = base.copy()
    changed[:, 5:] = 255
    frames = [(0, 0.0, base), (1, 0.1, changed)]
    gated = gate_frames_by_ssim(frames, threshold=0.01)
    assert len(gated) == 2


def test_gate_frames_by_ssim_filters_similar() -> None:
    base = np.zeros((10, 10, 3), dtype=np.uint8)
    almost = base.copy()
    almost[:, 0:2] = 1
    frames = [(0, 0.0, base), (1, 0.1, almost)]
    gated = gate_frames_by_ssim(frames, threshold=0.1)
    assert len(gated) == 1


def test_create_bundles_respects_max_frames() -> None:
    roi = ROIMetadata(x=0, y=0, width=2, height=2, confidence=1.0)
    frames = [(idx, float(idx), np.zeros((2, 2, 3), dtype=np.uint8)) for idx in range(5)]
    config = PreprocessConfig(bundle_max_frames=2)
    bundles = create_bundles(frames, roi, config)
    assert all(len(bundle.frame_indices) <= 2 for bundle in bundles)


def test_create_bundles_respects_time_gap() -> None:
    roi = ROIMetadata(x=0, y=0, width=2, height=2, confidence=1.0)
    frames = [
        (0, 0.0, np.zeros((2, 2, 3), dtype=np.uint8)),
        (1, 0.5, np.zeros((2, 2, 3), dtype=np.uint8)),
        (2, 5.0, np.zeros((2, 2, 3), dtype=np.uint8)),
    ]
    config = PreprocessConfig(bundle_max_time_gap_sec=2.0)
    bundles = create_bundles(frames, roi, config)
    assert len(bundles) == 2


def test_create_bundles_empty_input() -> None:
    roi = ROIMetadata(x=0, y=0, width=2, height=2, confidence=1.0)
    bundles = create_bundles([], roi, PreprocessConfig())
    assert bundles == []


def test_preprocess_video_end_to_end(
    synthetic_video: Path, default_config: PreprocessConfig
) -> None:
    bundles = preprocess_video(synthetic_video, default_config)
    assert bundles
    assert all(bundle.frames for bundle in bundles)


def test_preprocess_video_with_custom_config(synthetic_video: Path) -> None:
    custom_config = PreprocessConfig(target_fps=4.0, ssim_threshold=0.002, bundle_max_frames=3)
    bundles = preprocess_video(synthetic_video, custom_config)
    assert bundles
    assert all(len(bundle.frame_indices) <= 3 for bundle in bundles)


def test_preprocess_video_missing_file(default_config: PreprocessConfig) -> None:
    missing_path = Path("/tmp/nonexistent.mp4")
    with pytest.raises(PreprocessingError):
        preprocess_video(missing_path, default_config)


def test_build_frame_time_mapping() -> None:
    roi = ROIMetadata(x=0, y=0, width=2, height=2, confidence=1.0)
    bundle = FrameBundle(
        frames=(np.zeros((2, 2, 3), dtype=np.uint8),),
        frame_indices=(5,),
        timestamps_sec=(0.833,),
        roi=roi,
    )
    mapping = build_frame_time_mapping([bundle])
    assert "Frame→Time (s):" in mapping
    assert "5 -> 0.833" in mapping
