"""Data providers. Use `get_provider()` instead of importing a provider directly."""

from .base import CandleSet, DataProvider, ProviderError


def get_provider(name: str) -> DataProvider:
    if name == "yfinance":
        from .yfinance_provider import YFinanceProvider

        return YFinanceProvider()
    raise ProviderError(f"Unknown data provider {name!r}. Available: yfinance")


__all__ = ["CandleSet", "DataProvider", "ProviderError", "get_provider"]
