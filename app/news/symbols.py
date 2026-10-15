import re

from app.news.models import Asset

# Symbol -> (asset type, display name, keywords that indicate the asset is mentioned).
DEFAULT_ASSETS: dict[str, tuple[str, str, list[str]]] = {
    "BTCUSDT": ("crypto", "Bitcoin", ["bitcoin", "btc"]),
    "ETHUSDT": ("crypto", "Ethereum", ["ethereum", "ether", "eth"]),
    "SOLUSDT": ("crypto", "Solana", ["solana"]),
    "XRPUSDT": ("crypto", "XRP", ["xrp", "ripple"]),
    "DOGEUSDT": ("crypto", "Dogecoin", ["dogecoin", "doge"]),
    "AAPL": ("stock", "Apple", ["apple", "aapl"]),
    "MSFT": ("stock", "Microsoft", ["microsoft", "msft"]),
    "NVDA": ("stock", "NVIDIA", ["nvidia", "nvda"]),
    "TSLA": ("stock", "Tesla", ["tesla", "tsla"]),
    "AMZN": ("stock", "Amazon", ["amazon", "amzn"]),
    "GOOGL": ("stock", "Alphabet", ["alphabet", "google", "googl"]),
    "META": ("stock", "Meta", ["meta platforms", "facebook"]),
    "SAN.MC": ("stock", "Banco Santander", ["santander"]),
    "ITX.MC": ("stock", "Inditex", ["inditex"]),
}

_NAME_NOISE = re.compile(
    r"\b(inc|corp|corporation|company|co|ltd|plc|s\.?a|n\.?v|ag|se|holdings?|group|class [a-z]|banco)\b\.?", re.I
)


def keywords_from_name(name: str) -> list[str]:
    """'NVIDIA Corporation' -> ['nvidia']; 'Banco Santander, S.A.' -> ['santander']."""
    core = _NAME_NOISE.sub(" ", name.replace(",", " ")).strip(" .")
    core = re.sub(r"\s+", " ", core).lower()
    return [core] if len(core) >= 4 else []


class SymbolDetector:
    def __init__(self, assets: dict[str, tuple[str, str, list[str]]] | None = None):
        self._assets: dict[str, tuple[str, str, list[str]]] = dict(assets or DEFAULT_ASSETS)
        self._compile()

    def _compile(self) -> None:
        self._patterns = {
            sym: re.compile(r"\b(" + "|".join(re.escape(k) for k in kws) + r")\b", re.I)
            for sym, (_, _, kws) in self._assets.items() if kws
        }

    def add(self, symbol: str, type_: str, name: str) -> None:
        """Teach the detector a watchlist asset, keeping any hand-written keywords it already has."""
        symbol = symbol.upper()
        if symbol in self._assets:
            return
        ticker = symbol.split(".")[0].removesuffix("USDT").lower()
        kws = keywords_from_name(name) + ([ticker] if len(ticker) >= 3 else [])
        self._assets[symbol] = (type_, name, kws)
        self._compile()

    def detect(self, *texts: str | None) -> list[str]:
        blob = " ".join(t for t in texts if t)
        return sorted(sym for sym, pat in self._patterns.items() if pat.search(blob))

    def assets(self, symbols: list[str]) -> list[Asset]:
        return [Asset(symbol=s, type=self._assets[s][0], name=self._assets[s][1]) for s in symbols if s in self._assets]
