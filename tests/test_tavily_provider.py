from config.settings import settings
from src.data_providers import TavilyProvider


def test_tavily_is_optional_when_key_is_empty() -> None:
    if not settings.has_tavily():
        try:
            TavilyProvider()
        except RuntimeError as exc:
            assert "TAVILY_API_KEY" in str(exc)
        else:
            raise AssertionError("TavilyProvider should require TAVILY_API_KEY")
