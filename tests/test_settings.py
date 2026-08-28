from config.settings import settings


def test_settings_loads_without_real_keys() -> None:
    assert settings.project_root.exists()
    assert settings.request_timeout_seconds > 0
    assert settings.request_max_retries >= 0
