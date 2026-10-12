"""Whisper's existing log-Mel calculation with bounded FFT working memory.

Only the numerical work is batched, not the audio or ASR request. The full
recording, global dynamic-range normalization, frame order, padding, model,
and decoder remain unchanged. No response or timestamp is cut at a batch edge.
"""
from __future__ import annotations

import numpy as np

CONTRACT = "full-recording-logmel-bounded-fft-v1"


class BoundedFeatureExtractor:
    def __init__(self, original, frames_per_batch: int = 2048):
        if frames_per_batch < 1:
            raise ValueError("positive feature batch size required")
        self.original = original
        self.frames_per_batch = frames_per_batch

    def __getattr__(self, name):
        return getattr(self.original, name)

    def __call__(self, waveform, padding=160, chunk_length=None):
        source = self.original
        if chunk_length is not None:
            source.n_samples = chunk_length * source.sampling_rate
            source.nb_max_frames = source.n_samples // source.hop_length
        waveform = np.asarray(waveform, dtype=np.float32)
        if waveform.ndim != 1 or not waveform.size:
            raise ValueError("nonempty mono waveform required")
        if padding:
            waveform = np.pad(waveform, (0, padding))
        waveform = np.pad(waveform, (source.n_fft // 2, source.n_fft // 2), mode="reflect")
        # The original drops the final STFT frame globally, not per batch.
        frames = (waveform.size - source.n_fft) // source.hop_length
        window = np.hanning(source.n_fft + 1)[:-1].astype(np.float32)
        result = np.empty((source.mel_filters.shape[0], frames), dtype=np.float32)
        for first in range(0, frames, self.frames_per_batch):
            count = min(self.frames_per_batch, frames - first)
            offset = first * source.hop_length
            view = np.lib.stride_tricks.as_strided(
                waveform[offset:], shape=(count, source.n_fft),
                strides=(source.hop_length * waveform.strides[0], waveform.strides[0]),
                writeable=False,
            )
            stft = np.fft.rfft(view * window, n=source.n_fft, axis=-1).astype(np.complex64).T
            mel = source.mel_filters @ (np.abs(stft) ** 2)
            np.maximum(mel, 1e-10, out=mel)
            np.log10(mel, out=mel)
            result[:, first:first + count] = mel
        # Preserve the whole-recording maximum used by faster-whisper.
        np.maximum(result, result.max() - 8.0, out=result)
        result += 4.0
        result /= 4.0
        return result


def install(model):
    model.feature_extractor = BoundedFeatureExtractor(model.feature_extractor)
