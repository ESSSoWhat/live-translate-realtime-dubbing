"""
Processing module - VAD, pipeline, and audio processing.
"""

from live_dubbing.processing.pipeline import ProcessingPipeline
from live_dubbing.processing.vad import SileroVAD, VADResult

__all__ = ["SileroVAD", "VADResult", "ProcessingPipeline"]
