"""
Speaker identification using MFCC-based voice embeddings.

Computes compact speaker embeddings from audio and matches them
against registered speakers using cosine similarity.  No extra
dependencies beyond numpy and scipy (both already required).
"""

from __future__ import annotations

import numpy as np
import structlog
from scipy.fft import dct  # type: ignore[import-untyped]

logger = structlog.get_logger(__name__)


class SpeakerIdentifier:
    """Identify registered speakers from short audio clips.

    Workflow
    --------
    1. ``register_speaker(voice_id, audio)`` — store an embedding.
    2. ``identify(audio)`` — compare against all registered embeddings.
    3. ``best_match`` returns ``(voice_id, score)`` or ``(None, 0.0)``.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        n_mfcc: int = 20,
        n_mels: int = 40,
        similarity_threshold: float = 0.60,
    ) -> None:
        self._sample_rate = sample_rate
        self._n_mfcc = n_mfcc
        self._n_mels = n_mels
        self._threshold = similarity_threshold

        # voice_id → L2-normalised embedding
        self._embeddings: dict[str, np.ndarray] = {}

        # Pre-compute mel filterbank (reused for every embedding)
        self._n_fft = 512
        self._mel_fb = self._make_mel_filterbank(n_mels, self._n_fft, sample_rate)

    # ── public API ────────────────────────────────────────────────────────

    def register_speaker(self, voice_id: str, audio: np.ndarray) -> None:
        """Register (or update) a speaker embedding for *voice_id*."""
        if len(audio) < self._sample_rate * 0.5:
            logger.warning(
                "Audio too short for reliable embedding",
                voice_id=voice_id,
                duration_sec=len(audio) / self._sample_rate,
            )
            return
        emb = self._compute_embedding(audio)
        self._embeddings[voice_id] = emb
        logger.info(
            "Speaker embedding registered",
            voice_id=voice_id,
            duration_sec=round(len(audio) / self._sample_rate, 1),
        )

    def unregister_speaker(self, voice_id: str) -> None:
        """Remove a speaker embedding."""
        self._embeddings.pop(voice_id, None)

    def identify(self, audio: np.ndarray) -> tuple[str | None, float]:
        """Return ``(voice_id, confidence)`` of the best match, or ``(None, 0)``."""
        if not self._embeddings or len(audio) < self._sample_rate * 0.3:
            return None, 0.0

        emb = self._compute_embedding(audio)

        best_id: str | None = None
        best_score = -1.0

        for voice_id, stored in self._embeddings.items():
            score = float(np.dot(emb, stored))  # cosine sim (both L2-normed)
            if score > best_score:
                best_score = score
                best_id = voice_id

        if best_score >= self._threshold:
            return best_id, best_score

        return None, best_score

    @property
    def has_multiple_speakers(self) -> bool:
        """True when ≥2 speakers are registered (speaker-switching is useful)."""
        return len(self._embeddings) >= 2

    @property
    def speaker_count(self) -> int:
        return len(self._embeddings)

    # ── embedding computation ─────────────────────────────────────────────

    def _compute_embedding(self, audio: np.ndarray) -> np.ndarray:
        """Return an L2-normalised speaker embedding vector."""
        # Pre-emphasis
        emph = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])

        # Frame parameters
        frame_len = self._n_fft
        hop = 160  # 10 ms at 16 kHz
        n_frames = max(1, (len(emph) - frame_len) // hop)

        window = np.hamming(frame_len)
        frames = np.stack(
            [emph[i * hop : i * hop + frame_len] * window for i in range(n_frames)]
        )

        # Power spectrum
        mag = np.abs(np.fft.rfft(frames, n=frame_len))
        power = mag ** 2

        # Mel filterbank → log energies
        mel = np.dot(power, self._mel_fb.T)
        mel = np.maximum(mel, 1e-10)
        log_mel = np.log(mel)

        # DCT → MFCCs  (skip c0)
        all_coeffs = np.asarray(dct(log_mel, type=2, axis=1, norm="ortho"))
        mfccs = all_coeffs[:, 1 : self._n_mfcc + 1]

        # Delta MFCCs (first derivative)
        deltas = np.diff(mfccs, axis=0, prepend=mfccs[:1])

        # Statistical pooling → fixed-length vector
        embedding = np.concatenate(
            [
                mfccs.mean(axis=0),
                mfccs.std(axis=0),
                deltas.mean(axis=0),
                deltas.std(axis=0),
            ]
        )

        # L2 normalise
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding /= norm

        return embedding

    # ── mel filterbank construction ───────────────────────────────────────

    @staticmethod
    def _make_mel_filterbank(
        n_mels: int, n_fft: int, sample_rate: int
    ) -> np.ndarray:
        """Triangular mel-scale filterbank matrix (n_mels × n_fft//2+1)."""

        def _hz2mel(hz: float) -> float:
            return 2595.0 * np.log10(1.0 + hz / 700.0)

        def _mel2hz(m: float) -> float:
            return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

        low_mel = _hz2mel(0)
        high_mel = _hz2mel(sample_rate / 2)
        mel_pts = np.linspace(low_mel, high_mel, n_mels + 2)
        hz_pts = np.array([_mel2hz(m) for m in mel_pts])
        bins = np.floor((n_fft + 1) * hz_pts / sample_rate).astype(int)

        fb = np.zeros((n_mels, n_fft // 2 + 1))
        for i in range(n_mels):
            for j in range(bins[i], bins[i + 1]):
                denom = bins[i + 1] - bins[i]
                fb[i, j] = (j - bins[i]) / denom if denom else 0
            for j in range(bins[i + 1], bins[i + 2]):
                denom = bins[i + 2] - bins[i + 1]
                fb[i, j] = (bins[i + 2] - j) / denom if denom else 0

        return fb
