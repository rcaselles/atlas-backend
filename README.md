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

- Los símbolos salen de la lista de seguimiento (ver abajo).
- Si se corta, reconecta solo con espera creciente de 1 a 60 segundos, y si el endpoint no responde prueba el siguiente.
- El último precio y la variación de 24 h quedan en memoria para el resto del sistema, y se envían al front como mensajes `price`, como mucho uno por símbolo y segundo.
- `ATLAS_MARKET_STREAMING=false` lo desactiva (los tests lo desactivan y usan mensajes simulados).

## Acciones y lista de seguimiento

Las acciones salen del endpoint público de gráficos de Yahoo Finance (`query1.finance.yahoo.com/v8/finance/chart`), gratuito y sin clave.

- **Retraso:** el que da Yahoo para cada bolsa, normalmente de tiempo casi real a 15 minutos. Por eso las acciones van marcadas con `delayed: true`.
- **Límites:** es un endpoint no oficial y sin límites publicados, así que lo usamos con cuidado: cada acción se consulta una vez por minuto como mucho (`ATLAS_STOCK_POLL_SECONDS`, mínimo 30) y hay una caché de 60 segundos por símbolo. Como no es oficial, podría cambiar sin aviso; si deja de funcionar, solo afecta a las acciones.
- **Lista de seguimiento:** por defecto BTC, ETH, SOL, AAPL, MSFT, NVDA, TSLA y SAN.MC, configurable con `ATLAS_DEFAULT_WATCHLIST`. Se edita con `POST /api/watchlist` y `DELETE /api/watchlist/{symbol}`, se valida que el símbolo exista y se guarda en `ATLAS_WATCHLIST_FILE`. En un hosting gratuito ese archivo puede perderse al reiniciar, y entonces vuelve la lista por defecto.
- Al cambiar la lista, Binance y el sondeo de acciones se actualizan solos, sin reiniciar el servidor.

## Velas (gráfico)

`GET /api/candles/{símbolo}?interval=1m|1h|1d&limit=300` devuelve velas OHLC listas para Lightweight Charts. Cripto usa el endpoint público de klines de Binance (`data-api.binance.vision`, sin clave) y acciones el mismo endpoint de gráficos de Yahoo que las cotizaciones, con sus mismas limitaciones. El histórico se cachea en memoria y la vela en curso se actualiza en vivo con cada cotización y se empuja por el websocket a quien esté suscrito (`subscribe_candles`). Coste: cero.

## Secretos

Este repositorio es público. Toda la configuración va por variables de entorno (ver `.env.example`); nunca se suben claves.
