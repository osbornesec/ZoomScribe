"""Screenshare preprocessing utilities for Zoom screenshare content."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
from skimage.metrics import structural_similarity

__all__ = [
    "FrameBundle",
    "PreprocessConfig",
    "PreprocessingError",
    "ROIMetadata",
    "build_frame_time_mapping",
    "detect_roi",
    "preprocess_video",
]

FrameArray = np.ndarray[Any, np.dtype[np.uint8]]

GRAYSCALE_DIMENSION = 2
_SSIM = cast(Callable[..., float], structural_similarity)


class PreprocessingError(RuntimeError):
    """Raised when screenshare preprocessing fails."""


@dataclass(frozen=True, slots=True)
class PreprocessConfig:
    """Configuration for screenshare preprocessing steps.

    Args:
        target_fps: Target frames-per-second sampling rate (default 6.0).
        roi_detection_duration_sec: Duration of initial video segment, in seconds, to
            inspect for ROI detection (default 10.0).
        ssim_threshold: Minimum change threshold (1 - SSIM) to keep a frame (default
            0.005).
        bundle_max_frames: Maximum number of frames per bundle (default 6).
        bundle_max_time_gap_sec: Maximum allowed time gap, in seconds, between frames
            in a single bundle (default 2.0).

    Raises:
        ValueError: If any provided parameter violates constraints.
    """

    target_fps: float = 6.0
    roi_detection_duration_sec: float = 10.0
    ssim_threshold: float = 0.005
    bundle_max_frames: int = 6
    bundle_max_time_gap_sec: float = 2.0

    def __post_init__(self) -> None:
        """
        Validate PreprocessConfig field invariants.
        
        Raises:
            ValueError: If any of the following conditions are violated:
                - `target_fps` is not greater than 0.
                - `roi_detection_duration_sec` is not greater than 0.
                - `ssim_threshold` is not greater than 0.
                - `bundle_max_frames` is not greater than 0.
                - `bundle_max_time_gap_sec` is negative.
        """
        if self.target_fps <= 0:
            raise ValueError("target_fps must be greater than 0")
        if self.roi_detection_duration_sec <= 0:
            raise ValueError("roi_detection_duration_sec must be greater than 0")
        if self.ssim_threshold <= 0:
            raise ValueError("ssim_threshold must be greater than 0")
        if self.bundle_max_frames <= 0:
            raise ValueError("bundle_max_frames must be greater than 0")
        if self.bundle_max_time_gap_sec < 0:
            raise ValueError("bundle_max_time_gap_sec must be non-negative")


@dataclass(frozen=True, slots=True)
class ROIMetadata:
    """Region of interest metadata detected within a screenshare."""

    x: int
    y: int
    width: int
    height: int
    confidence: float

    def __post_init__(self) -> None:
        """
        Validate that ROI dimensions and confidence are within allowed ranges.
        
        Raises:
            ValueError: If `width` or `height` is less than or equal to 0, or if `confidence` is not between 0.0 and 1.0 inclusive.
        """
        if self.width <= 0 or self.height <= 0:
            raise ValueError("ROI dimensions must be greater than 0")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("ROI confidence must be between 0.0 and 1.0 inclusive")


@dataclass(frozen=True, slots=True)
class FrameBundle:
    """Ordered bundle of frames sampled from a screenshare."""

    frames: tuple[FrameArray, ...]
    frame_indices: tuple[int, ...]
    timestamps_sec: tuple[float, ...]
    roi: ROIMetadata

    def __post_init__(self) -> None:
        """
        Ensure the FrameBundle's frames, frame_indices, and timestamps_sec are non-empty and have equal lengths.
        
        Raises:
            ValueError: If the three sequences do not all have the same length or if any sequence is empty.
        """
        lengths = {
            len(self.frames),
            len(self.frame_indices),
            len(self.timestamps_sec),
        }
        if len(lengths) != 1 or not lengths:
            raise ValueError("FrameBundle inputs must have equal, non-zero lengths")
        if len(self.frames) == 0:
            raise ValueError("FrameBundle must contain at least one frame")

    @property
    def duration_sec(self) -> float:
        """
        Compute the time span covered by the bundle's timestamps.
        
        Returns:
            Duration in seconds between the first and last timestamp in `timestamps_sec`; returns 0.0 if the bundle contains fewer than two timestamps.
        """
        if len(self.timestamps_sec) <= 1:
            return 0.0
        return self.timestamps_sec[-1] - self.timestamps_sec[0]


def detect_roi(video_path: Path, config: PreprocessConfig) -> ROIMetadata:
    """
    Detect the primary region of interest (ROI) in a screenshare video.
    
    Scans up to config.roi_detection_duration_sec seconds of the input video and identifies the most prominent rectangular region
    containing foreground content; returns its coordinates, size, and a confidence score in [0.0, 1.0]. The function opens the video
    file and always releases the underlying capture resource before returning.
    
    Parameters:
        video_path (Path): Path to the input video file; must exist and be a regular file.
        config (PreprocessConfig): Configuration controlling ROI detection duration and related parameters.
    
    Returns:
        ROIMetadata: Detected ROI with x, y, width, height, and confidence (0.0–1.0).
    
    Raises:
        PreprocessingError: If video_path is missing or not a file, the video cannot be opened/read, or no ROI is found.
    """
    if not video_path.is_file():
        raise PreprocessingError("Video path does not exist or is not a file")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise PreprocessingError("Failed to open video")

    try:
        native_fps = capture.get(cv2.CAP_PROP_FPS)
        if native_fps <= 0:
            native_fps = 30.0
        max_frames = int(native_fps * config.roi_detection_duration_sec)
        if max_frames <= 0:
            max_frames = int(native_fps)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

        best_score = -1.0
        best_roi: tuple[int, int, int, int, float] | None = None

        frame_index = 0
        while frame_index < max_frames:
            success, frame = capture.read()
            if not success or frame is None:
                break

            gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            _, thresholded = cv2.threshold(gray_frame, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            closed = cv2.morphologyEx(thresholded, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            frame_area = float(frame.shape[0] * frame.shape[1])
            for contour in contours:
                x, y, width, height = cv2.boundingRect(contour)
                if width <= 0 or height <= 0:
                    continue
                region = closed[y : y + height, x : x + width]
                if region.size == 0:
                    continue
                foreground_density = float(np.count_nonzero(region)) / float(region.size)
                score = float(width * height) * foreground_density
                if score > best_score:
                    confidence = min(1.0, score / frame_area) if frame_area > 0 else 0.0
                    best_score = score
                    best_roi = (x, y, width, height, confidence)

            frame_index += 1

        if best_roi is None:
            raise PreprocessingError("ROI detection failed")

        x, y, width, height, confidence = best_roi
        return ROIMetadata(x=x, y=y, width=width, height=height, confidence=confidence)
    finally:
        capture.release()


def extract_frames_at_fps(
    video_path: Path, target_fps: float, roi: ROIMetadata
) -> list[tuple[int, float, FrameArray]]:
    """
    Sample and crop frames from a video at a fixed target frame rate using the provided ROI.
    
    The function opens the video file, determines the native FPS (falls back to 30.0 if unavailable),
    and samples frames by skipping frames so the effective sampling rate approximates `target_fps`.
    Each sampled frame is timestamped as `frame_index / native_fps` and cropped to the supplied
    `roi` (crop bounds are clamped to the frame bounds). The video capture is always released before
    returning.
    
    Parameters:
        video_path (Path): Path to the input video file; must exist and be a regular file.
        target_fps (float): Desired sampling frequency in frames per second; must be > 0.
        roi (ROIMetadata): Region of interest used to crop each sampled frame (width/height must be > 0).
    
    Returns:
        list[tuple[int, float, FrameArray]]: A list of tuples (frame_index, timestamp_sec, cropped_frame)
        for each sampled and cropped frame. `timestamp_sec` is a floating-point number in seconds.
    
    Raises:
        PreprocessingError: If the video path does not exist, the video cannot be opened,
        the ROI lies outside frame bounds, or if no frames were extracted.
    
    Side effects:
        - Opens the video file with cv2.VideoCapture and performs I/O while reading frames.
        - Always releases the video capture before returning.
        - Not safe for concurrent writes to the same video file; reading the same file concurrently is allowed.
    """
    if not video_path.is_file():
        raise PreprocessingError("Video path does not exist or is not a file")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise PreprocessingError("Failed to open video")

    frames: list[tuple[int, float, FrameArray]] = []
    try:
        native_fps = capture.get(cv2.CAP_PROP_FPS)
        if native_fps <= 0:
            native_fps = 30.0
        skip = max(1, round(native_fps / target_fps))

        frame_index = 0
        while True:
            success, frame = capture.read()
            if not success or frame is None:
                break

            if frame_index % skip == 0:
                timestamp = frame_index / native_fps
                y_start = max(0, roi.y)
                x_start = max(0, roi.x)
                y_end = min(frame.shape[0], y_start + roi.height)
                x_end = min(frame.shape[1], x_start + roi.width)
                if y_end <= y_start or x_end <= x_start:
                    raise PreprocessingError("ROI is outside the frame bounds")
                cropped = frame[y_start:y_end, x_start:x_end].copy()
                frames.append((frame_index, float(timestamp), cast(FrameArray, cropped)))

            frame_index += 1
    finally:
        capture.release()

    if not frames:
        raise PreprocessingError("No frames extracted")

    return frames


def gate_frames_by_ssim(
    frames: list[tuple[int, float, FrameArray]], threshold: float
) -> list[tuple[int, float, FrameArray]]:
    """
    Filter a sequence of frames by structural dissimilarity to remove near-duplicate frames.
    
    Keeps the first frame (if any) and then retains any subsequent frame whose change from the last kept frame,
    measured as (1 - SSIM), is greater than or equal to `threshold`. Returns an empty list if `frames` is empty.
    
    Args:
        frames: List of tuples (frame_index, timestamp_sec, frame_array). `frame_array` may be color or grayscale.
        threshold: Minimum required dissimilarity (1 - SSIM) to keep a frame; higher values produce more aggressive filtering.
    
    Returns:
        A list of the input frame tuples that passed the SSIM gating, preserving original order.
    
    Notes:
        - This function does not modify input frames.
        - No exceptions are raised by this function.
    """
    if not frames:
        return []

    gated: list[tuple[int, float, FrameArray]] = [frames[0]]
    prev_gray = _to_gray(frames[0][2])

    for frame_index, timestamp, frame_data in frames[1:]:
        current_gray = _to_gray(frame_data)
        ssim = _SSIM(prev_gray, current_gray, data_range=255)
        if 1.0 - ssim >= threshold:
            gated.append((frame_index, timestamp, frame_data))
            prev_gray = current_gray

    return gated


def create_bundles(
    gated_frames: list[tuple[int, float, FrameArray]],
    roi: ROIMetadata,
    config: PreprocessConfig,
) -> list[FrameBundle]:
    """
    Group a sequence of SSIM-gated frames into ordered FrameBundle objects according to size and time-gap constraints.
    
    Given a list of retained frames (index, timestamp_sec, frame), this function accumulates contiguous frames into bundles and emits a FrameBundle whenever the current bundle reaches config.bundle_max_frames or the time gap between consecutive frames exceeds config.bundle_max_time_gap_sec. Each returned bundle contains the bundled frames, their original frame indices and timestamps, and the provided ROI metadata.
    
    Parameters:
        gated_frames (list[tuple[int, float, FrameArray]]): Sequence of retained frames as (frame_index, timestamp_sec, frame). Frames are processed in list order; an empty list yields an empty result.
        roi (ROIMetadata): Region-of-interest metadata associated with all frames in the bundles.
        config (PreprocessConfig): Configuration that controls bundling via `bundle_max_frames` and `bundle_max_time_gap_sec`.
    
    Returns:
        list[FrameBundle]: Ordered list of FrameBundle objects covering the input frames. Each bundle has at least one frame and preserves the original ordering and timestamps.
    """
    if not gated_frames:
        return []

    bundles: list[FrameBundle] = []
    current_frames: list[FrameArray] = []
    current_indices: list[int] = []
    current_timestamps: list[float] = []

    for frame_index, timestamp, frame_data in gated_frames:
        if current_frames:
            exceeds_length = len(current_frames) >= config.bundle_max_frames
            exceeds_gap = timestamp - current_timestamps[-1] > config.bundle_max_time_gap_sec
            if exceeds_length or exceeds_gap:
                bundles.append(
                    FrameBundle(
                        frames=tuple(current_frames),
                        frame_indices=tuple(current_indices),
                        timestamps_sec=tuple(current_timestamps),
                        roi=roi,
                    )
                )
                current_frames = []
                current_indices = []
                current_timestamps = []

        current_frames.append(frame_data)
        current_indices.append(frame_index)
        current_timestamps.append(timestamp)

    if current_frames:
        bundles.append(
            FrameBundle(
                frames=tuple(current_frames),
                frame_indices=tuple(current_indices),
                timestamps_sec=tuple(current_timestamps),
                roi=roi,
            )
        )

    return bundles


def preprocess_video(video_path: Path, config: PreprocessConfig | None = None) -> list[FrameBundle]:
    """
    Preprocess a screenshare video into ROI-focused, time-ordered frame bundles.
    
    Orchestrates the end-to-end pipeline: detect the primary region of interest (ROI), sample and crop frames at the configured frame rate, filter frames by structural similarity changes, and group the resulting frames into time-bounded bundles.
    
    Args:
        video_path (Path): Path to the input video file; must exist and be readable.
        config (PreprocessConfig | None): Optional preprocessing configuration; when omitted, a default PreprocessConfig() is used to control sampling rate, ROI detection duration, SSIM threshold, and bundling constraints.
    
    Returns:
        list[FrameBundle]: A list of FrameBundle objects produced by the pipeline. Returns an empty list if no frames pass SSIM gating.
    
    Raises:
        PreprocessingError: If any stage of preprocessing fails (for example, video cannot be opened, ROI detection fails, ROI is out of frame bounds, or frame extraction yields no frames).
    
    Side effects:
        Opens the video file(s) for reading and releases associated resources before returning.
    
    Concurrency:
        Stateless and does not rely on shared mutable module-level state; safe to call concurrently from multiple threads or processes.
    """
    cfg = config or PreprocessConfig()
    roi = detect_roi(video_path, cfg)
    frames = extract_frames_at_fps(video_path, cfg.target_fps, roi)
    gated_frames = gate_frames_by_ssim(frames, cfg.ssim_threshold)
    if not gated_frames:
        return []
    return create_bundles(gated_frames, roi, cfg)


def build_frame_time_mapping(bundles: list[FrameBundle]) -> str:
    """
    Produce a human-readable mapping of frame indices to their timestamps across provided bundles.
    
    Parameters:
        bundles (list[FrameBundle]): Sequence of FrameBundle objects whose `frame_indices` and `timestamps_sec`
            will be flattened and mapped. Bundles may overlap in frame indices; mapping lines are sorted by frame index.
    
    Returns:
        str: A multi-line string beginning with "Frame→Time (s):" and followed by lines of the form
            "<frame_index> -> <timestamp>" where timestamps are formatted with three decimal places.
    
    Notes:
        - The function has no side effects and is safe to call concurrently.
        - It assumes each FrameBundle's `frame_indices` and `timestamps_sec` are aligned (equal lengths), as enforced
          by the FrameBundle class.
    """
    mapping: list[tuple[int, float]] = []
    for bundle in bundles:
        mapping.extend(zip(bundle.frame_indices, bundle.timestamps_sec, strict=False))

    mapping.sort(key=lambda item: item[0])

    lines = ["Frame→Time (s):"]
    for frame_index, timestamp in mapping:
        lines.append(f"{frame_index} -> {timestamp:.3f}")

    return "\n".join(lines)


def _to_gray(frame: FrameArray) -> FrameArray:
    """
    Convert a video frame to a 2D grayscale image.
    
    Parameters:
        frame (FrameArray): Input image. If `frame.ndim == 2` it is treated as already grayscale; otherwise it is expected to be a BGR color image.
    
    Returns:
        FrameArray: A 2-dimensional grayscale image (same dtype as input). If the input was already grayscale, the same array is returned unchanged.
    """
    if frame.ndim == GRAYSCALE_DIMENSION:
        return frame
    return cast(FrameArray, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
