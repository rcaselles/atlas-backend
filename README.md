# Atlas Backend

Backend de Atlas, un terminal de mercados futurista y gratuito: noticias financieras en tiempo real (RSS y scraping respetuoso), cotizaciones cripto en directo y de acciones con retraso, e histórico de velas, todo enviado al frontend por websocket.

Stack: Python 3.13, FastAPI, Uvicorn y pytest. Solo fuentes de datos gratuitas.

## Arrancar en local

```bash
make install   # crea .venv e instala dependencias
cp .env.example .env
make dev       # http://localhost:8000 (documentación interactiva en /docs)
```

## Tests

```bash
make test
```

GitHub Actions ejecuta los tests en cada push y pull request.

## API

- `GET /health` devuelve el estado del servicio.
- Contrato completo para el frontend: [docs/API.md](docs/API.md).

## Noticias

Las fuentes están en [config/sources.yaml](config/sources.yaml) y se cambian sin tocar código. Ahora hay cinco RSS públicos: CNBC, MarketWatch, Investing.com, Cointelegraph y Expansión Mercados.

- Cada fuente se consulta cada 120 a 180 segundos (configurable, mínimo 60) con un User-Agent identificable (`AtlasBot`).
- El scraping HTML (`type: html`) solo se usa para webs sin RSS: comprueba `robots.txt` antes de cada descarga y se salta la página si no está permitida.
- Las noticias se normalizan (título, fuente, URL, fecha UTC, resumen y activos detectados) y se eliminan duplicados por URL y por título.
- Si una fuente falla, las demás siguen funcionando. `GET /api/news/sources` muestra el estado de cada una.
- Para trabajar sin conexión, pon `ATLAS_NEWS_POLLING=false`. Los tests nunca llaman a internet: usan respuestas grabadas en `tests/fixtures`.

## Cotizaciones cripto

Llegan en tiempo real por el websocket público de Binance (stream `miniTicker`, sin clave ni coste). Por defecto se usa `data-stream.binance.vision`, el endpoint de solo datos de mercado, que funciona desde más regiones, y `stream.binance.com` queda de respaldo.

- Los símbolos se configuran con `ATLAS_CRYPTO_SYMBOLS` (por defecto BTC, ETH y SOL contra USDT).
- Si se corta, reconecta solo con espera creciente de 1 a 60 segundos, y si el endpoint no responde prueba el siguiente.
- El último precio y la variación de 24 h quedan en memoria para el resto del sistema, y se envían al front como mensajes `price`, como mucho uno por símbolo y segundo.
- `ATLAS_MARKET_STREAMING=false` lo desactiva (los tests lo desactivan y usan mensajes simulados).

## Secretos

Este repositorio es público. Toda la configuración va por variables de entorno (ver `.env.example`); nunca se suben claves.
