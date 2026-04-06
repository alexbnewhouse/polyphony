"""Tests for polyphony.onboarding — hardware detection & recommendations."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from polyphony.onboarding import (
    GPUInfo,
    HardwareProfile,
    ModelRecommendation,
    OnboardingResult,
    WhisperRecommendation,
    _check_faster_whisper,
    _check_pyannote,
    _classify_tier,
    detect_hardware,
    generate_recommendations,
    run_onboarding,
)


# ─── Tier classification ─────────────────────────────────────────────────────


def _make_hw(**kwargs) -> HardwareProfile:
    """Create a HardwareProfile with sensible defaults overridden by kwargs."""
    defaults = dict(
        os_name="Linux",
        os_version="6.5",
        arch="x86_64",
        cpu_cores=8,
        ram_total_mb=16384,
        gpus=[],
        apple_silicon=False,
        ollama_installed=False,
        ollama_running=False,
        ollama_models=[],
    )
    defaults.update(kwargs)
    return HardwareProfile(**defaults)


class TestClassifyTier:
    def test_high_nvidia_gpu(self):
        hw = _make_hw(
            ram_total_mb=32768,
            gpus=[GPUInfo(name="RTX 4090", vram_mb=24576)],
        )
        assert _classify_tier(hw) == "local_high"

    def test_mid_nvidia_gpu(self):
        hw = _make_hw(
            ram_total_mb=16384,
            gpus=[GPUInfo(name="RTX 3060", vram_mb=6144)],
        )
        assert _classify_tier(hw) == "local_mid"

    def test_low_cpu_only(self):
        hw = _make_hw(ram_total_mb=8192, gpus=[])
        assert _classify_tier(hw) == "local_low"

    def test_cloud_only_low_ram(self):
        hw = _make_hw(ram_total_mb=4096, gpus=[])
        assert _classify_tier(hw) == "cloud_only"

    def test_apple_silicon_high(self):
        hw = _make_hw(ram_total_mb=32768, apple_silicon=True, os_name="Darwin")
        assert _classify_tier(hw) == "local_high"

    def test_apple_silicon_mid(self):
        hw = _make_hw(ram_total_mb=16384, apple_silicon=True, os_name="Darwin")
        assert _classify_tier(hw) == "local_mid"

    def test_apple_silicon_low(self):
        hw = _make_hw(ram_total_mb=8192, apple_silicon=True, os_name="Darwin")
        assert _classify_tier(hw) == "local_low"

    def test_apple_silicon_cloud_only(self):
        hw = _make_hw(ram_total_mb=4096, apple_silicon=True, os_name="Darwin")
        assert _classify_tier(hw) == "cloud_only"


# ─── Recommendations ─────────────────────────────────────────────────────────


class TestGenerateRecommendations:
    def test_local_high_recommends_8b(self):
        hw = _make_hw(
            ram_total_mb=32768,
            gpus=[GPUInfo(name="RTX 4090", vram_mb=24576)],
        )
        result = generate_recommendations(hw)
        assert result.tier == "local_high"
        local_recs = [r for r in result.recommendations if r.provider == "ollama"]
        assert len(local_recs) >= 1
        assert any("8b" in r.model_name for r in local_recs)

    def test_local_mid_recommends_3b(self):
        hw = _make_hw(
            ram_total_mb=16384,
            gpus=[GPUInfo(name="RTX 3060", vram_mb=6144)],
        )
        result = generate_recommendations(hw)
        assert result.tier == "local_mid"
        local_recs = [r for r in result.recommendations if r.provider == "ollama"]
        assert any("3b" in r.model_name for r in local_recs)

    def test_cloud_only_warns(self):
        hw = _make_hw(ram_total_mb=4096, gpus=[])
        result = generate_recommendations(hw)
        assert result.tier == "cloud_only"
        assert len(result.warnings) > 0
        assert any("cloud" in w.lower() or "insufficient" in w.lower() for w in result.warnings)

    def test_always_includes_cloud_options(self):
        hw = _make_hw(
            ram_total_mb=32768,
            gpus=[GPUInfo(name="RTX 4090", vram_mb=24576)],
        )
        result = generate_recommendations(hw)
        providers = {r.provider for r in result.recommendations}
        assert "openai" in providers
        assert "anthropic" in providers

    def test_setup_steps_include_ollama_install_when_missing(self):
        hw = _make_hw(ollama_installed=False)
        result = generate_recommendations(hw)
        assert any("install" in step.lower() and "ollama" in step.lower()
                    for step in result.setup_steps)

    def test_setup_steps_include_ollama_serve_when_not_running(self):
        hw = _make_hw(ollama_installed=True, ollama_running=False)
        result = generate_recommendations(hw)
        assert any("ollama serve" in step.lower() or "start" in step.lower()
                    for step in result.setup_steps)

    def test_setup_steps_include_api_key_when_missing(self):
        hw = _make_hw(ram_total_mb=4096)  # cloud_only
        result = generate_recommendations(hw)
        assert any("openai" in step.lower() or "anthropic" in step.lower()
                    for step in result.setup_steps)

    def test_no_duplicate_model_pull_when_already_installed(self):
        hw = _make_hw(
            ram_total_mb=32768,
            gpus=[GPUInfo(name="RTX 4090", vram_mb=24576)],
            ollama_installed=True,
            ollama_running=True,
            ollama_models=["llama3.1:8b"],
        )
        result = generate_recommendations(hw)
        assert not any("pull" in step and "llama3.1:8b" in step
                        for step in result.setup_steps)


# ─── Hardware detection (mocked) ─────────────────────────────────────────────


class TestDetectHardware:
    @patch("polyphony.onboarding._check_ollama", return_value=(True, True, ["llama3.1:8b"]))
    @patch("polyphony.onboarding._detect_nvidia_gpus", return_value=[])
    @patch("polyphony.onboarding._detect_apple_silicon", return_value=False)
    @patch("polyphony.onboarding._detect_ram_mb", return_value=16384)
    @patch("polyphony.onboarding._detect_cpu_cores", return_value=8)
    def test_basic_detection(self, mock_cpu, mock_ram, mock_apple, mock_gpu, mock_ollama):
        hw = detect_hardware()
        assert hw.ram_total_mb == 16384
        assert hw.cpu_cores == 8
        assert hw.ollama_running is True
        assert "llama3.1:8b" in hw.ollama_models


# ─── Data classes ─────────────────────────────────────────────────────────────


class TestDataClasses:
    def test_gpu_info_vram_gb(self):
        g = GPUInfo(name="Test GPU", vram_mb=8192)
        assert g.vram_gb == 8.0

    def test_hw_profile_ram_gb(self):
        hw = _make_hw(ram_total_mb=16384)
        assert hw.ram_gb == 16.0

    def test_hw_profile_best_gpu_no_gpus(self):
        hw = _make_hw(gpus=[])
        assert hw.best_gpu_vram_mb == 0

    def test_hw_profile_best_gpu_with_gpus(self):
        hw = _make_hw(gpus=[
            GPUInfo(name="GPU A", vram_mb=4096),
            GPUInfo(name="GPU B", vram_mb=8192),
        ])
        assert hw.best_gpu_vram_mb == 8192

    def test_hw_profile_has_gpu(self):
        assert _make_hw(gpus=[GPUInfo(name="X", vram_mb=1024)]).has_gpu is True
        assert _make_hw(apple_silicon=True).has_gpu is True
        assert _make_hw().has_gpu is False

    def test_model_recommendation_fields(self):
        r = ModelRecommendation(
            provider="ollama", model_name="llama3.1:8b",
            label="Test", reason="Test reason",
            estimated_speed="fast", estimated_cost="free",
        )
        assert r.requires_api_key is False
        assert r.supports_vision is False

    def test_whisper_recommendation_fields(self):
        w = WhisperRecommendation(
            model_size="small",
            label="Test Whisper",
            reason="Test reason",
            estimated_speed="fast",
            estimated_vram_gb=1.5,
        )
        assert w.local is True
        assert w.estimated_vram_gb == 1.5

    def test_whisper_recommendation_cloud(self):
        w = WhisperRecommendation(
            model_size="whisper-1",
            label="Cloud",
            reason="Cloud",
            estimated_speed="fast",
            estimated_vram_gb=0.0,
            local=False,
        )
        assert w.local is False


# ─── Whisper recommendations ─────────────────────────────────────────────────


class TestWhisperRecommendations:
    def test_local_high_recommends_large_v3(self):
        hw = _make_hw(
            ram_total_mb=32768,
            gpus=[GPUInfo(name="RTX 4090", vram_mb=24576)],
        )
        result = generate_recommendations(hw)
        local_whisper = [w for w in result.whisper_recommendations if w.local]
        assert any("large-v3" in w.model_size for w in local_whisper)

    def test_local_mid_recommends_small(self):
        hw = _make_hw(
            ram_total_mb=16384,
            gpus=[GPUInfo(name="RTX 3060", vram_mb=6144)],
        )
        result = generate_recommendations(hw)
        local_whisper = [w for w in result.whisper_recommendations if w.local]
        assert any("small" in w.model_size for w in local_whisper)

    def test_local_low_recommends_base(self):
        hw = _make_hw(ram_total_mb=8192, gpus=[])
        result = generate_recommendations(hw)
        local_whisper = [w for w in result.whisper_recommendations if w.local]
        assert any("base" in w.model_size for w in local_whisper)

    def test_cloud_only_has_no_local_whisper(self):
        hw = _make_hw(ram_total_mb=4096, gpus=[])
        result = generate_recommendations(hw)
        local_whisper = [w for w in result.whisper_recommendations if w.local]
        assert len(local_whisper) == 0

    def test_always_includes_cloud_whisper(self):
        hw = _make_hw(
            ram_total_mb=32768,
            gpus=[GPUInfo(name="RTX 4090", vram_mb=24576)],
        )
        result = generate_recommendations(hw)
        cloud_whisper = [w for w in result.whisper_recommendations if not w.local]
        assert len(cloud_whisper) >= 1
        assert any("whisper-1" in w.model_size for w in cloud_whisper)

    def test_onboarding_result_has_audio_fields(self):
        hw = _make_hw(ram_total_mb=16384)
        result = generate_recommendations(hw)
        assert isinstance(result.faster_whisper_installed, bool)
        assert isinstance(result.pyannote_installed, bool)


# ─── Audio dependency detection ──────────────────────────────────────────────


class TestAudioDetection:
    @patch.dict("sys.modules", {"faster_whisper": None})
    def test_check_faster_whisper_missing(self):
        # When the import raises ImportError, should return False
        with patch("builtins.__import__", side_effect=ImportError):
            assert _check_faster_whisper() is False

    @patch.dict("sys.modules", {"pyannote": None, "pyannote.audio": None})
    def test_check_pyannote_missing(self):
        with patch("builtins.__import__", side_effect=ImportError):
            assert _check_pyannote() is False

    def test_setup_steps_include_audio_when_missing(self):
        hw = _make_hw(ram_total_mb=16384)
        with patch("polyphony.onboarding._check_faster_whisper", return_value=False), \
             patch("polyphony.onboarding._check_pyannote", return_value=False):
            result = generate_recommendations(hw)
        assert any("polyphony[audio]" in step for step in result.setup_steps)
        assert any("polyphony[diarize]" in step for step in result.setup_steps)

    def test_no_audio_setup_steps_when_installed(self):
        hw = _make_hw(ram_total_mb=16384)
        with patch("polyphony.onboarding._check_faster_whisper", return_value=True), \
             patch("polyphony.onboarding._check_pyannote", return_value=True):
            result = generate_recommendations(hw)
        assert not any("polyphony[audio]" in step for step in result.setup_steps)
        assert not any("polyphony[diarize]" in step for step in result.setup_steps)


# ─── Integration ──────────────────────────────────────────────────────────────


class TestRunOnboarding:
    @patch("polyphony.onboarding._check_ollama", return_value=(False, False, []))
    @patch("polyphony.onboarding._detect_nvidia_gpus", return_value=[])
    @patch("polyphony.onboarding._detect_apple_silicon", return_value=False)
    @patch("polyphony.onboarding._detect_ram_mb", return_value=16384)
    @patch("polyphony.onboarding._detect_cpu_cores", return_value=4)
    def test_end_to_end(self, *mocks):
        result = run_onboarding()
        assert isinstance(result, OnboardingResult)
        assert result.hardware.ram_total_mb == 16384
        assert len(result.recommendations) > 0
        assert result.tier in ("local_high", "local_mid", "local_low", "cloud_only")
