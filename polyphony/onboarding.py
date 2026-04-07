"""
polyphony.onboarding
====================
Hardware detection and LLM setup recommendations.

Detects local hardware capabilities (RAM, GPU, VRAM) and recommends
appropriate LLM configurations — local models via Ollama for capable
machines, or cloud providers for lighter setups.

Used by both CLI (``polyphony setup``) and GUI (Settings page).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field



@dataclass
class GPUInfo:
    """Information about a detected GPU."""
    name: str
    vram_mb: int
    driver: str = ""

    @property
    def vram_gb(self) -> float:
        return self.vram_mb / 1024


@dataclass
class HardwareProfile:
    """Summary of the local machine's hardware capabilities."""
    os_name: str
    os_version: str
    arch: str
    cpu_cores: int
    ram_total_mb: int
    gpus: list[GPUInfo] = field(default_factory=list)
    apple_silicon: bool = False
    ollama_installed: bool = False
    ollama_running: bool = False
    ollama_models: list[str] = field(default_factory=list)

    @property
    def ram_gb(self) -> float:
        return self.ram_total_mb / 1024

    @property
    def best_gpu_vram_mb(self) -> int:
        return max((g.vram_mb for g in self.gpus), default=0)

    @property
    def has_gpu(self) -> bool:
        return len(self.gpus) > 0 or self.apple_silicon


@dataclass
class ModelRecommendation:
    """A recommended model configuration."""
    provider: str          # "ollama", "openai", "anthropic"
    model_name: str        # e.g. "llama3.1:8b", "gpt-4o-mini"
    label: str             # Human-readable description
    reason: str            # Why this is recommended
    estimated_speed: str   # "fast", "moderate", "slow"
    estimated_cost: str    # "free", "$", "$$", "$$$"
    supports_vision: bool = False
    requires_api_key: bool = False


@dataclass
class WhisperRecommendation:
    """A recommended Whisper transcription model."""
    model_size: str        # "tiny", "base", "small", "medium", "large-v3"
    label: str
    reason: str
    estimated_speed: str   # "fast", "moderate", "slow"
    estimated_vram_gb: float  # approximate VRAM/RAM needed
    local: bool = True     # True = faster-whisper, False = OpenAI API
    compute_type: str = "auto"  # "auto", "float16", "int8", "float32"


@dataclass
class OnboardingResult:
    """Complete onboarding assessment."""
    hardware: HardwareProfile
    tier: str              # "local_high", "local_mid", "local_low", "cloud_only"
    recommendations: list[ModelRecommendation] = field(default_factory=list)
    whisper_recommendations: list[WhisperRecommendation] = field(default_factory=list)
    setup_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    faster_whisper_installed: bool = False
    pyannote_installed: bool = False


# ─── Hardware detection ──────────────────────────────────────────────────────


def _detect_ram_mb() -> int:
    """Detect total system RAM in MB."""
    try:
        import psutil
        return int(psutil.virtual_memory().total / (1024 * 1024))
    except ImportError:
        pass

    # Fallback: read /proc/meminfo on Linux
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024  # KB → MB
    except (OSError, ValueError):
        pass

    # Fallback: sysctl on macOS
    try:
        out = subprocess.check_output(["sysctl", "-n", "hw.memsize"],
                                      stderr=subprocess.DEVNULL, timeout=5)
        return int(out.strip()) // (1024 * 1024)
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        pass

    return 0


def _detect_cpu_cores() -> int:
    """Detect logical CPU cores."""
    return os.cpu_count() or 1


def _detect_nvidia_gpus() -> list[GPUInfo]:
    """Detect NVIDIA GPUs via nvidia-smi."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=10,
        )
        gpus = []
        for line in out.decode().strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                name = parts[0]
                vram_mb = int(float(parts[1]))
                driver = parts[2] if len(parts) > 2 else ""
                gpus.append(GPUInfo(name=name, vram_mb=vram_mb, driver=driver))
        return gpus
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        return []


def _detect_apple_silicon() -> bool:
    """Check if running on Apple Silicon."""
    if platform.system() != "Darwin":
        return False
    return platform.machine() in ("arm64", "aarch64")


def _check_ollama() -> tuple[bool, bool, list[str]]:
    """Check Ollama installation, running status, and installed models."""
    installed = shutil.which("ollama") is not None

    running = False
    models: list[str] = []

    if installed:
        try:
            import json
            import logging
            import urllib.parse
            import urllib.request

            logger = logging.getLogger("polyphony.onboarding")
            host = os.environ.get(
                "POLYPHONY_OLLAMA_HOST", "http://localhost:11434"
            )
            # Only allow http/https schemes
            parsed = urllib.parse.urlparse(host)
            if parsed.scheme not in ("http", "https"):
                return installed, False, []

            # Validate hostname is not an internal/private address
            hostname = parsed.hostname or ""
            if not hostname or hostname in (
                "169.254.169.254",  # Cloud metadata endpoint
            ):
                logger.warning("Blocked unsafe Ollama host: %s", hostname)
                return installed, False, []

            with urllib.request.urlopen(  # noqa: S310
                f"{host}/", timeout=3
            ):
                running = True

            req = urllib.request.Request(
                f"{host}/api/tags",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(  # noqa: S310
                req, timeout=5
            ) as resp:
                data = json.loads(resp.read().decode())
                models = [m["name"] for m in data.get("models", [])]
        except Exception:
            logger.debug("Ollama check failed", exc_info=True)

    return installed, running, models


def _check_faster_whisper() -> bool:
    """Check if faster-whisper is installed."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _check_pyannote() -> bool:
    """Check if pyannote.audio is installed."""
    try:
        import pyannote.audio  # noqa: F401
        return True
    except ImportError:
        return False


def detect_hardware() -> HardwareProfile:
    """Detect local hardware and return a HardwareProfile."""
    system = platform.system()
    version = platform.version()
    arch = platform.machine()
    cpu_cores = _detect_cpu_cores()
    ram_mb = _detect_ram_mb()
    gpus = _detect_nvidia_gpus()
    apple_silicon = _detect_apple_silicon()
    ollama_installed, ollama_running, ollama_models = _check_ollama()

    return HardwareProfile(
        os_name=system,
        os_version=version,
        arch=arch,
        cpu_cores=cpu_cores,
        ram_total_mb=ram_mb,
        gpus=gpus,
        apple_silicon=apple_silicon,
        ollama_installed=ollama_installed,
        ollama_running=ollama_running,
        ollama_models=ollama_models,
    )


# ─── Recommendation engine ──────────────────────────────────────────────────


def _classify_tier(hw: HardwareProfile) -> str:
    """Classify hardware into a capability tier.

    Tiers:
    - local_high: 16+ GB RAM + GPU with 8+ GB VRAM, or Apple Silicon with 32+ GB
    - local_mid:  8+ GB RAM + some GPU, or Apple Silicon with 16+ GB
    - local_low:  Can run small models (3B params) but slowly
    - cloud_only: Not enough resources for local inference
    """
    vram = hw.best_gpu_vram_mb

    # Apple Silicon unified memory
    if hw.apple_silicon:
        if hw.ram_gb >= 32:
            return "local_high"
        elif hw.ram_gb >= 16:
            return "local_mid"
        elif hw.ram_gb >= 8:
            return "local_low"
        return "cloud_only"

    # NVIDIA GPU
    if vram >= 8192 and hw.ram_gb >= 16:
        return "local_high"
    if vram >= 4096 and hw.ram_gb >= 8:
        return "local_mid"
    if hw.ram_gb >= 8:
        return "local_low"

    return "cloud_only"


def generate_recommendations(hw: HardwareProfile) -> OnboardingResult:
    """Generate LLM setup recommendations based on hardware profile."""
    tier = _classify_tier(hw)
    recommendations: list[ModelRecommendation] = []
    setup_steps: list[str] = []
    warnings: list[str] = []

    # ── Local model recommendations ──────────────────────────────────
    if tier == "local_high":
        recommendations.append(ModelRecommendation(
            provider="ollama",
            model_name="llama3.1:8b",
            label="Llama 3.1 8B — Recommended for most QDA projects",
            reason=f"Your hardware ({hw.ram_gb:.0f} GB RAM"
                   + (f", {hw.gpus[0].name}" if hw.gpus else ", Apple Silicon")
                   + ") can run 8B models comfortably.",
            estimated_speed="fast",
            estimated_cost="free",
        ))
        recommendations.append(ModelRecommendation(
            provider="ollama",
            model_name="llava:7b",
            label="LLaVA 7B — For image + text coding",
            reason="Vision-capable model for multimodal QDA with your hardware.",
            estimated_speed="moderate",
            estimated_cost="free",
            supports_vision=True,
        ))

    elif tier == "local_mid":
        recommendations.append(ModelRecommendation(
            provider="ollama",
            model_name="llama3.2:3b",
            label="Llama 3.2 3B — Good balance for your hardware",
            reason=f"Your hardware ({hw.ram_gb:.0f} GB RAM) works well with 3B parameter models.",
            estimated_speed="moderate",
            estimated_cost="free",
        ))
        recommendations.append(ModelRecommendation(
            provider="ollama",
            model_name="llama3.1:8b-q4_0",
            label="Llama 3.1 8B (4-bit quantized) — Higher quality, slower",
            reason="Quantized 8B model fits in limited VRAM with some speed tradeoff.",
            estimated_speed="slow",
            estimated_cost="free",
        ))

    elif tier == "local_low":
        recommendations.append(ModelRecommendation(
            provider="ollama",
            model_name="llama3.2:3b",
            label="Llama 3.2 3B — CPU inference (slow but functional)",
            reason=f"With {hw.ram_gb:.0f} GB RAM and no dedicated GPU, "
                   "small models run on CPU. Expect 15–30 seconds per segment.",
            estimated_speed="slow",
            estimated_cost="free",
        ))
        warnings.append(
            "Local inference will be slow without a GPU. "
            "Consider using a cloud provider for larger corpora (100+ segments)."
        )

    # ── Cloud recommendations (always included as alternatives) ──────
    recommendations.append(ModelRecommendation(
        provider="openai",
        model_name="gpt-4o-mini",
        label="GPT-4o Mini — Fast & affordable cloud option",
        reason="Best value cloud model. ~$0.15 per 1M input tokens. "
               "Good for corpora of any size.",
        estimated_speed="fast",
        estimated_cost="$",
        supports_vision=True,
        requires_api_key=True,
    ))
    recommendations.append(ModelRecommendation(
        provider="openai",
        model_name="gpt-4o",
        label="GPT-4o — Highest quality cloud model (OpenAI)",
        reason="Best coding quality. ~$2.50 per 1M input tokens. "
               "Recommended for high-stakes research.",
        estimated_speed="fast",
        estimated_cost="$$",
        supports_vision=True,
        requires_api_key=True,
    ))
    recommendations.append(ModelRecommendation(
        provider="anthropic",
        model_name="claude-sonnet-4-6",
        label="Claude Sonnet 4.6 — High quality (Anthropic)",
        reason="Strong qualitative reasoning. Note: Anthropic does not support "
               "deterministic seeds, so reproducibility is best-effort.",
        estimated_speed="fast",
        estimated_cost="$$",
        supports_vision=True,
        requires_api_key=True,
    ))

    if tier == "cloud_only":
        warnings.append(
            f"Your system ({hw.ram_gb:.0f} GB RAM, no GPU) "
            "is insufficient for local LLM inference. "
            "We recommend cloud providers (OpenAI or Anthropic)."
        )

    # ── Audio transcription (Whisper) recommendations ────────────────
    whisper_recs: list[WhisperRecommendation] = []
    faster_whisper_ok = _check_faster_whisper()
    pyannote_ok = _check_pyannote()

    # Determine optimal compute type: float16 for GPU tiers, int8 for CPU-only
    gpu_compute = "float16" if hw.has_gpu else "int8"

    if tier == "local_high":
        whisper_recs.append(WhisperRecommendation(
            model_size="large-v3",
            label="Whisper Large V3 — Best accuracy",
            reason="Your GPU can handle the largest Whisper model for "
                   "highest-quality transcription.",
            estimated_speed="moderate",
            estimated_vram_gb=4.0,
            compute_type=gpu_compute,
        ))
        whisper_recs.append(WhisperRecommendation(
            model_size="medium",
            label="Whisper Medium — Good balance of speed & accuracy",
            reason="Faster than large-v3 with minimal quality loss.",
            estimated_speed="fast",
            estimated_vram_gb=2.5,
            compute_type=gpu_compute,
        ))
    elif tier == "local_mid":
        whisper_recs.append(WhisperRecommendation(
            model_size="small",
            label="Whisper Small — Recommended for your hardware",
            reason="Best accuracy that fits comfortably in your available VRAM.",
            estimated_speed="fast",
            estimated_vram_gb=1.5,
            compute_type=gpu_compute,
        ))
        whisper_recs.append(WhisperRecommendation(
            model_size="medium",
            label="Whisper Medium — Higher quality, slower",
            reason="Fits in VRAM but will be noticeably slower.",
            estimated_speed="moderate",
            estimated_vram_gb=2.5,
            compute_type=gpu_compute,
        ))
    elif tier == "local_low":
        whisper_recs.append(WhisperRecommendation(
            model_size="base",
            label="Whisper Base — Lightweight for CPU inference",
            reason="Reasonable quality at low resource cost. "
                   "Runs on CPU without a GPU.",
            estimated_speed="moderate",
            estimated_vram_gb=0.5,
            compute_type="int8",
        ))
        whisper_recs.append(WhisperRecommendation(
            model_size="tiny",
            label="Whisper Tiny — Fastest, lowest accuracy",
            reason="Transcribes quickly on CPU but quality may suffer "
                   "with accents or technical vocabulary.",
            estimated_speed="fast",
            estimated_vram_gb=0.3,
            compute_type="int8",
        ))

    # Always offer cloud Whisper as alternative
    whisper_recs.append(WhisperRecommendation(
        model_size="whisper-1",
        label="OpenAI Whisper API — Cloud transcription",
        reason="No local resources needed. 25 MB file-size limit per request. "
               "~$0.006/min. Requires OPENAI_API_KEY.",
        estimated_speed="fast",
        estimated_vram_gb=0.0,
        local=False,
    ))

    # ── Setup steps ──────────────────────────────────────────────────
    if tier != "cloud_only":
        if not hw.ollama_installed:
            setup_steps.append(
                "Install Ollama: visit https://ollama.ai "
                "or run `curl -fsSL https://ollama.ai/install.sh | sh`"
            )
        elif not hw.ollama_running:
            setup_steps.append("Start Ollama: run `ollama serve` in a separate terminal")

        # Recommend pulling the top local model
        top_local = next((r for r in recommendations if r.provider == "ollama"), None)
        if top_local and top_local.model_name not in hw.ollama_models:
            setup_steps.append(f"Pull model: run `ollama pull {top_local.model_name}`")

    # Audio setup steps
    if not faster_whisper_ok:
        if hw.has_gpu:
            setup_steps.append(
                "For GPU-accelerated audio transcription: pip install 'polyphony[audio-gpu]' "
                "(installs faster-whisper + CUDA libraries for float16 inference)"
            )
        else:
            setup_steps.append(
                "For audio transcription: pip install 'polyphony[audio]' "
                "(installs faster-whisper for local Whisper)"
            )
    elif hw.has_gpu:
        # faster-whisper installed but might not have CUDA libs
        setup_steps.append(
            "For GPU-accelerated transcription: pip install 'polyphony[audio-gpu]' "
            "(ensures CUDA libraries are available for float16 on your GPU)"
        )
    if not pyannote_ok:
        setup_steps.append(
            "For speaker diarization: pip install 'polyphony[diarize]' "
            "(requires HF_TOKEN — see https://huggingface.co/pyannote/speaker-diarization-3.1)"
        )

    if any(r.provider == "openai" for r in recommendations):
        if not os.environ.get("OPENAI_API_KEY"):
            setup_steps.append("Set OpenAI key: export OPENAI_API_KEY='sk-...' (get one at https://platform.openai.com/api-keys)")

    if any(r.provider == "anthropic" for r in recommendations):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            setup_steps.append(
                "Set Anthropic key: export "
                "ANTHROPIC_API_KEY='sk-ant-...' "
                "(get one at https://console.anthropic.com/)"
            )

    return OnboardingResult(
        hardware=hw,
        tier=tier,
        recommendations=recommendations,
        whisper_recommendations=whisper_recs,
        setup_steps=setup_steps,
        warnings=warnings,
        faster_whisper_installed=faster_whisper_ok,
        pyannote_installed=pyannote_ok,
    )


def run_onboarding() -> OnboardingResult:
    """Full onboarding: detect hardware and generate recommendations."""
    hw = detect_hardware()
    return generate_recommendations(hw)
