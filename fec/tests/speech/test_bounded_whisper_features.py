import numpy as np
import pytest

from fec.speech.transcribe_member_longform_local_asr import install_av_policy_shim
from fec.speech.bounded_whisper_features import BoundedFeatureExtractor


@pytest.mark.parametrize("length,padding,batch", [(160, 160, 7), (16123, 0, 17), (99201, 160, 127)])
def test_matches_original_across_fft_batches(length, padding, batch):
    install_av_policy_shim()
    from faster_whisper.feature_extractor import FeatureExtractor
    original = FeatureExtractor()
    audio = np.random.default_rng(20260904).normal(0, .1, length).astype(np.float32)
    expected = original(audio, padding=padding)
    actual = BoundedFeatureExtractor(original, batch)(audio, padding=padding)
    assert actual.shape == expected.shape
    assert actual.dtype == expected.dtype
    np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)


def test_global_normalization_not_per_batch():
    install_av_policy_shim()
    from faster_whisper.feature_extractor import FeatureExtractor
    original = FeatureExtractor()
    audio = np.random.default_rng(7).normal(0, .1, 80000).astype(np.float32)
    audio[:40000] *= 1e-6
    np.testing.assert_allclose(BoundedFeatureExtractor(original, 31)(audio), original(audio), rtol=1e-6, atol=1e-6)


def test_preserves_config_and_refuses_invalid_batch():
    install_av_policy_shim()
    from faster_whisper.feature_extractor import FeatureExtractor
    original = FeatureExtractor()
    wrapped = BoundedFeatureExtractor(original, 100)
    wrapped(np.ones(8000, dtype=np.float32), chunk_length=60)
    assert wrapped.n_samples == original.n_samples == 60 * 16000
    assert wrapped.nb_max_frames == original.nb_max_frames == 6000
    with pytest.raises(ValueError):
        BoundedFeatureExtractor(original, 0)
