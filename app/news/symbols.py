import re

# Symbol -> keywords that indicate the asset is mentioned. Extended later by the watchlist (task 4).
DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "ether", "eth"],
    "SOLUSDT": ["solana"],
    "AAPL": ["apple", "aapl"],
    "MSFT": ["microsoft", "msft"],
    "NVDA": ["nvidia", "nvda"],
    "TSLA": ["tesla", "tsla"],
    "AMZN": ["amazon", "amzn"],
    "GOOGL": ["alphabet", "google", "googl"],
    "META": ["meta platforms", "facebook"],
}


class SymbolDetector:
    def __init__(self, keywords: dict[str, list[str]] | None = None):
        self._patterns = {
            sym: re.compile(r"\b(" + "|".join(re.escape(k) for k in kws) + r")\b", re.I)
            for sym, kws in (keywords or DEFAULT_KEYWORDS).items()
        }

    def detect(self, *texts: str | None) -> list[str]:
        blob = " ".join(t for t in texts if t)
        return sorted(sym for sym, pat in self._patterns.items() if pat.search(blob))
