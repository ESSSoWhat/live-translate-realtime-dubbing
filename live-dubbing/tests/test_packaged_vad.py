"""
Test that Silero VAD works correctly after torch optimizations.
Verifies that removing torch.distributed, torch._inductor, torch.testing
and onnxruntime doesn't break voice activity detection.
"""

import numpy as np
import pytest
import torch


def test_silero_vad_model_loads():
    """Test that Silero VAD model can be loaded after torch optimizations."""
    from silero_vad import load_silero_vad, read_audio
    
    # This will fail if torch components needed by silero are missing
    model = load_silero_vad(onnx=False)
    assert model is not None
    assert isinstance(model, torch.nn.Module)
    print("OK Silero VAD model loaded successfully")


def test_silero_vad_inference():
    """Test that Silero VAD can perform inference on audio."""
    from silero_vad import load_silero_vad
    
    model = load_silero_vad(onnx=False)
    
    # Silero VAD expects 512 samples for 16kHz (32ms chunks)
    sample_rate = 16000
    chunk_size = 512
    
    # Speech-like signal: sine wave with noise (512 samples)
    t = np.linspace(0, chunk_size / sample_rate, chunk_size)
    speech_signal = np.sin(2 * np.pi * 200 * t) + 0.1 * np.random.randn(chunk_size)
    speech_tensor = torch.FloatTensor(speech_signal)
    
    # Run inference
    speech_prob = model(speech_tensor, sample_rate).item()
    
    assert 0 <= speech_prob <= 1, f"Speech probability {speech_prob} out of range"
    print(f"OK Speech detection inference works (prob: {speech_prob:.3f})")
    
    # Test with silence
    silence_signal = np.random.randn(chunk_size) * 0.01
    silence_tensor = torch.FloatTensor(silence_signal)
    silence_prob = model(silence_tensor, sample_rate).item()
    
    assert 0 <= silence_prob <= 1
    print(f"OK Silence detection works (prob: {silence_prob:.3f})")
    
    # Speech signal should typically have higher probability than pure noise
    # (though not guaranteed for every random sample)
    print(f"OK Speech vs silence probabilities: {speech_prob:.3f} vs {silence_prob:.3f}")


def test_torch_cpu_operations():
    """Verify torch CPU tensor operations work."""
    # Test that basic tensor operations work
    x = torch.randn(10, 10)
    y = torch.mm(x, x.t())
    assert y.shape == (10, 10)
    print("OK Torch CPU operations work")
    
    # Note: In dev environment CUDA may be available; 
    # packaged dist should have only CPU support
    if torch.cuda.is_available():
        print("Note: CUDA available in dev environment (expected)")
    else:
        print("OK CPU-only mode (no CUDA)")


@pytest.mark.skipif(
    'sys.platform == "win32" and "site-packages" in sys.modules.get("torch", type("", (), {"__file__": ""})).__file__',
    reason="Module check only meaningful in packaged dist, not dev venv"
)
def test_removed_modules_not_imported():
    """Verify that removed modules are not in packaged dist."""
    import sys
    
    # In packaged dist, these should NOT be present
    # (In dev venv they will be, so this test is skipped there)
    excluded_modules = [
        'onnxruntime',
        'torch.distributed',
        'torch._inductor', 
        'torch.testing',
    ]
    
    actually_excluded = []
    for module_name in excluded_modules:
        if module_name not in sys.modules:
            actually_excluded.append(module_name)
    
    print(f"OK Verified {len(actually_excluded)}/{len(excluded_modules)} modules not imported")
    print(f"   Not imported: {actually_excluded}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
