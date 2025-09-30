"""Screenshare transcript extraction pipeline for terminal and GUI actions."""

from .preprocess import FrameBundle, PreprocessConfig, ROIMetadata, preprocess_video

__all__ = [
    "FrameBundle",
    "PreprocessConfig",
    "ROIMetadata",
    "preprocess_video",
]
