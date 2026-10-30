"""Settings, read from environment variables (or a ``.env`` file in the project root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(value: str | None, default: float | None) -> float | None:
    if value is None or value.strip() == "":
        return default
    return float(value)


def _int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass
class Settings:
    model: str = "claude-opus-5-5"
    refusal_fallback: bool = True

    default_location: str = ""
    default_radius_miles: float | None = None
    fb_location_id: str = ""

    headless: bool = False
    browser_channel: str = ""
    chromium_executable: str = ""
    profile_dir: Path = PROJECT_ROOT / ".fb_profile"
    cache_dir: Path = PROJECT_ROOT / ".cache"
    results_dir: Path = PROJECT_ROOT / "results"

    max_results_per_query: int = 80
    max_deep_checks: int = 40
    images_per_listing: int = 3
    ai_workers: int = 4
    min_delay_seconds: float = 1.5
    max_delay_seconds: float = 4.0

    geocoder_user_agent: str = "facebook-marketplace-match-bot"


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    env = os.environ.get
    defaults = Settings()
    return Settings(
        model=env("CLAUDE_MODEL") or defaults.model,
        refusal_fallback=_bool(env("CLAUDE_REFUSAL_FALLBACK"), defaults.refusal_fallback),
        default_location=env("DEFAULT_LOCATION", "").strip(),
        default_radius_miles=_float(env("DEFAULT_RADIUS_MILES"), defaults.default_radius_miles),
        fb_location_id=env("FB_LOCATION_ID", "").strip().strip("/"),
        headless=_bool(env("HEADLESS"), defaults.headless),
        browser_channel=env("BROWSER_CHANNEL", "").strip(),
        chromium_executable=env("CHROMIUM_EXECUTABLE", "").strip(),
        profile_dir=Path(env("FB_PROFILE_DIR") or defaults.profile_dir),
        max_results_per_query=_int(env("MAX_RESULTS_PER_QUERY"), defaults.max_results_per_query),
        max_deep_checks=_int(env("MAX_DEEP_CHECKS"), defaults.max_deep_checks),
        images_per_listing=_int(env("IMAGES_PER_LISTING"), defaults.images_per_listing),
        ai_workers=_int(env("AI_WORKERS"), defaults.ai_workers),
        geocoder_user_agent=env("GEOCODER_USER_AGENT") or defaults.geocoder_user_agent,
    )
