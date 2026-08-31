from config.settings import _env_bool, _env_ratio, settings


def test_settings_loads_without_real_keys() -> None:
    assert settings.project_root.exists()
    assert settings.request_timeout_seconds > 0
    assert settings.request_max_retries >= 0
    assert isinstance(settings.market_cross_validate, bool)
    assert isinstance(settings.market_validate_calendar, bool)
    assert settings.market_close_diff_threshold >= 0
    assert settings.market_volume_diff_threshold >= 0
    assert settings.market_amount_diff_threshold >= 0


def test_environment_parsers_support_common_values(monkeypatch) -> None:
    monkeypatch.setenv("TEST_BOOL", "off")
    # Ratio 配置使用小数比例，避免百分数与小数的单位歧义。
    monkeypatch.setenv("TEST_RATIO", "0.0125")
    assert _env_bool("TEST_BOOL", True) is False
    assert _env_ratio("TEST_RATIO", 0.5) == 0.0125


def test_environment_parsers_reject_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("TEST_BOOL", "sometimes")
    monkeypatch.setenv("TEST_RATIO", "-0.1")
    try:
        _env_bool("TEST_BOOL", True)
    except ValueError as exc:
        assert "TEST_BOOL" in str(exc)
    else:
        raise AssertionError("invalid boolean should raise")
    try:
        _env_ratio("TEST_RATIO", 0.5)
    except ValueError as exc:
        assert "TEST_RATIO" in str(exc)
    else:
        raise AssertionError("negative ratio should raise")
