"""Runtime configuration model for Contrechamp.

Loads config.yaml, merges with env overrides, and provides typed access.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class BudgetMode(str, Enum):
    OBSERVE = "observe"
    WARN = "warn"
    CAP = "cap"


class CheckpointPolicy(str, Enum):
    GUIDED = "guided"
    MANUAL_ALL = "manual_all"
    AUTO_NONCREATIVE = "auto_noncreative"


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096


class BudgetConfig(BaseModel):
    mode: BudgetMode = BudgetMode.WARN
    total_usd: float = 10.0
    reserve_pct: float = 0.10
    single_action_approval_usd: float = 0.50
    require_approval_for_new_paid_tool: bool = True


class CheckpointConfig(BaseModel):
    policy: CheckpointPolicy = CheckpointPolicy.GUIDED
    storage_dir: str = "pipeline"


class OutputConfig(BaseModel):
    default_format: str = "mp4"
    default_codec: str = "libx264"
    default_audio_codec: str = "aac"
    default_resolution: str = "1920x1080"
    default_fps: int = 30
    default_crf: int = 23


class PathsConfig(BaseModel):
    pipeline_dir: str = "pipeline"
    library_dir: str = "library"
    styles_dir: str = "styles"
    skills_dir: str = "skills"
    output_dir: str = "output"


class ReviewConfig(BaseModel):
    """Thresholds of the measured post-render checks (lib/render_checks.py).

    Defaults were calibrated on a handful of real renders; tune them to your
    style. Every threshold is also reported next to its measure in final_review.
    """

    # A caption token with at least this many letters is glued words.
    glued_word_min_len: int = Field(21, ge=8, le=60)
    # Continuity cuts (hold, freeze, match) must stay above / below these.
    seam_min_ssim: float = Field(0.90, ge=0.0, le=1.0)
    seam_max_luma_jump: float = Field(4.0, ge=0.0, le=255.0)
    # Music bed: share of the audio allowed to be digital silence.
    music_max_silence_ratio: float = Field(0.05, ge=0.0, le=1.0)
    silence_noise_db: float = Field(-50.0, le=0.0)
    silence_min_seconds: float = Field(0.3, gt=0.0)
    # Expected on-screen text: loose-match floor, and "not there at all".
    text_match_min: float = Field(0.85, ge=0.0, le=1.0)
    text_present_min: float = Field(0.5, ge=0.0, le=1.0)
    black_frame_max_luma: float = Field(6.0, ge=0.0, le=255.0)
    max_sampled_segments: int = Field(24, ge=1, le=500)
    ocr_thresholds: list[int] = Field(default_factory=lambda: [140, 200])
    # When text is expected on screen but OCR (tesseract) cannot run:
    # True -> the review cannot "pass"; False -> recorded under not_checked only.
    ocr_required: bool = True
    # Narration loudness: below this integrated level the voice is too quiet
    # (a mixer once flattened a finished narration to about -30 LUFS). Kept
    # under broadcast R128 (-23 LUFS) so compliant masters never trip it.
    loudness_min_lufs: float = Field(-26.0, ge=-70.0, le=0.0)
    # Last sentence: it must end before the video does, and not sit this many
    # dB under the whole mix on its last WORD (a fade-out once swallowed the
    # last 4 seconds). Measured on 15 real sentences (a channel's voice and
    # its clones): a healthy last word sits -4.9 to 12.5 dB under the mix
    # (short final words fall off); a 1.3 s fade swallowing it, 35.7 dB. A
    # cut under a music bed (11.2 dB) is caught by listening, not by level.
    last_sentence_max_drop_db: float = Field(20.0, ge=0.0, le=60.0)
    last_sentence_min_margin_s: float = Field(0.1, ge=0.0, le=10.0)
    # The end of the final mix is transcribed: the last sentence must be found
    # in it (a voice cut under a music bed kept a normal level).
    last_sentence_min_heard: float = Field(0.6, ge=0.0, le=1.0)
    last_sentence_model: str = "small"


class ContrechampConfig(BaseModel):
    """Top-level runtime configuration."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "ContrechampConfig":
        """Load config from YAML file. Falls back to defaults if file missing."""
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent / "config.yaml"

        if config_path.exists():
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            return cls.model_validate(raw)

        return cls()

    def resolve_path(self, key: str, project_root: Optional[Path] = None) -> Path:
        """Resolve a relative path from PathsConfig against project root."""
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent
        value = getattr(self.paths, key)
        return (project_root / value).resolve()
