import pytest

from marketplace_bot.ai import ClaudeMatcher
from marketplace_bot.backends import BackendError, choose_backend
from marketplace_bot.config import Settings
from marketplace_bot.ollama_ai import OllamaMatcher, OllamaStatus
from marketplace_bot.rules import RulesMatcher


def running(url, model):
    return OllamaStatus(True, vision=True)


def not_installed(url, model):
    return OllamaStatus(False, message="Ollama isn't running on this computer.")


def test_auto_uses_local_ai_when_available():
    settings = Settings()
    backend = choose_backend(settings, check=running)
    assert isinstance(backend.matcher, OllamaMatcher) and backend.matcher.uses_images
    assert "free local AI" in backend.description
    assert (settings.max_deep_checks, settings.images_per_listing, settings.ai_workers) == (15, 2, 1)


def test_auto_falls_back_to_free_keyword_matching():
    settings = Settings()
    backend = choose_backend(settings, check=not_installed)
    assert isinstance(backend.matcher, RulesMatcher)
    assert "isn't running" in backend.tip and "README" in backend.tip
    assert (settings.max_deep_checks, settings.images_per_listing) == (40, 0)


def test_explicit_choices():
    assert isinstance(choose_backend(Settings(ai_backend="rules"), check=running).matcher, RulesMatcher)
    assert isinstance(choose_backend(Settings(ai_backend="claude")).matcher, ClaudeMatcher)
    with pytest.raises(BackendError, match="isn't running"):
        choose_backend(Settings(ai_backend="ollama"), check=not_installed)
    with pytest.raises(BackendError, match="must be one of"):
        choose_backend(Settings(ai_backend="gpt"))


def test_settings_from_env_win_over_backend_defaults():
    settings = Settings(max_deep_checks=5)
    choose_backend(settings, check=running)
    assert settings.max_deep_checks == 5
