"""Chooses how listings get matched: free keyword rules, a free local AI (Ollama), or paid Claude."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .ollama_ai import OllamaMatcher, check_ollama
from .rules import RulesMatcher

# Per-backend defaults for settings left blank in .env.
BACKEND_DEFAULTS = {
    "rules": {"max_deep_checks": 40, "images_per_listing": 0, "ai_workers": 1},
    "ollama": {"max_deep_checks": 15, "images_per_listing": 2, "ai_workers": 1},
    "claude": {"max_deep_checks": 40, "images_per_listing": 3, "ai_workers": 4},
}
CHOICES = ("auto", "rules", "ollama", "claude")


class BackendError(RuntimeError):
    pass


@dataclass
class Backend:
    name: str
    matcher: object
    description: str
    tip: str = ""


def choose_backend(settings: Settings, check=check_ollama) -> Backend:
    choice = (settings.ai_backend or "auto").strip().lower()
    if choice not in CHOICES:
        raise BackendError(f"AI_BACKEND must be one of {', '.join(CHOICES)} (got {settings.ai_backend!r}).")

    if choice == "claude":
        import anthropic

        from .ai import ClaudeMatcher

        backend = Backend(
            "claude",
            ClaudeMatcher(anthropic.Anthropic(), settings.model, settings.refusal_fallback),
            f"Claude ({settings.model}), paid per use",
        )
    elif choice in ("auto", "ollama"):
        status = check(settings.ollama_url, settings.ollama_model)
        if status.ok:
            sees = ", looks at photos" if status.vision else ""
            backend = Backend(
                "ollama",
                OllamaMatcher(settings.ollama_url, settings.ollama_model, status.vision),
                f"free local AI on your computer ({settings.ollama_model}{sees})",
            )
        elif choice == "ollama":
            raise BackendError(status.message + " See 'Smarter matching (free)' in the README.")
        else:
            backend = Backend(
                "rules",
                RulesMatcher(),
                "free keyword matching",
                tip=f"{status.message} For smarter matching that also checks photos, see "
                "'Smarter matching (free)' in the README.",
            )
    else:
        backend = Backend("rules", RulesMatcher(), "free keyword matching")

    for field, value in BACKEND_DEFAULTS[backend.name].items():
        if getattr(settings, field) is None:
            setattr(settings, field, value)
    return backend
