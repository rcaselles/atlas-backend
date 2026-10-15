# Contrato de API de Atlas (v0.1, borrador)

Base URL local: `http://localhost:8000`. En producción, la URL pública del backend (tarea 7).
Todas las fechas van en ISO 8601 UTC. Los timestamps de velas van en segundos Unix (formato de Lightweight Charts).

Símbolos: cripto con el par de Binance (`BTCUSDT`), acciones con su ticker (`AAPL`). El campo `type` vale `crypto` o `stock`.

## REST

### `GET /health`
```json
{ "status": "ok", "version": "0.1.0", "env": "development" }
```

### `GET /api/news?limit=20&cursor=<id>&symbol=AAPL`
Últimas noticias, de más nueva a más antigua. `cursor` es el `next_cursor` de la página anterior; `symbol` filtra opcionalmente. `limit` va de 1 a 100. Un `cursor` que ya no existe devuelve `400` con código `invalid_cursor`; en ese caso, pide la primera página de nuevo.
```json
{
  "items": [
    {
      "id": "c0ffee12",
      "title": "Titular de ejemplo",
      "source": "Nombre del medio",
      "url": "https://ejemplo.com/noticia",
      "published_at": "2026-10-15T14:20:00Z",
      "summary": "Primeras líneas, si la fuente las da",
      "symbols": ["AAPL"]
    }
  ],
  "next_cursor": "b4dbeef9"
}
```

### `GET /api/news/sources`
Estado de cada fuente de noticias, útil para un indicador en la interfaz.
```json
[
  { "id": "cnbc", "name": "CNBC", "type": "rss", "ok": true, "last_run": "2026-10-15T14:20:00Z", "last_error": null, "items_last_run": 30 }
]
```
`ok` es `null` si la fuente aún no se ha consultado.

### `GET /api/watchlist`
```json
{
  "items": [
    {
      "symbol": "BTCUSDT",
      "type": "crypto",
      "name": "Bitcoin",
      "price": 0.0,
      "change_24h_pct": 0.0,
      "updated_at": "2026-10-15T14:20:00Z",
      "delayed": false
    }
  ]
}
```
`delayed: true` indica que la cotización viene con retraso (acciones gratuitas).

### `POST /api/watchlist`
Cuerpo: `{ "symbol": "ETHUSDT", "type": "crypto" }`. Devuelve la lista completa. `409` si ya existe, `422` si el símbolo no es válido.

### `DELETE /api/watchlist/{symbol}`
Devuelve la lista completa. `404` si no estaba.

### `GET /api/candles/{symbol}?interval=1h&limit=300`
`interval`: `1m`, `1h` o `1d`. Respuesta lista para `series.setData()` de Lightweight Charts:
```json
{
  "symbol": "BTCUSDT",
  "interval": "1h",
  "candles": [
    { "time": 1760536800, "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0 }
  ]
}
```

### Errores
Siempre `{ "error": { "code": "not_found", "message": "texto legible" } }` con el código HTTP correspondiente.

## Websocket `GET /ws`

El servidor empuja mensajes JSON con la forma `{ "type": "...", "data": { ... } }`.

Estado actual: `snapshot`, `news`, `candle`, `ping` y las suscripciones ya funcionan. Los mensajes `price` llegarán con la tarea 3, y hasta la tarea 4 la `watchlist` del snapshot viene vacía.

Al conectar, el servidor envía un `snapshot` con la lista de seguimiento y las últimas noticias:
```json
{ "type": "snapshot", "data": { "watchlist": [ /* como GET /api/watchlist */ ], "news": [ /* como GET /api/news */ ] } }
```

Cambio de precio (como mucho uno por símbolo y segundo):
```json
{ "type": "price", "data": { "symbol": "BTCUSDT", "price": 0.0, "change_24h_pct": 0.0, "updated_at": "2026-10-15T14:20:01Z", "delayed": false } }
```

Noticia nueva:
```json
{ "type": "news", "data": { /* un item como en GET /api/news */ } }
```

Vela en curso (solo para los símbolos suscritos):
```json
{ "type": "candle", "data": { "symbol": "BTCUSDT", "interval": "1m", "candle": { "time": 1760536800, "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0 } } }
```

Mensajes del cliente al servidor:
```json
{ "type": "subscribe_candles", "data": { "symbol": "BTCUSDT", "interval": "1m" } }
{ "type": "unsubscribe_candles", "data": { "symbol": "BTCUSDT", "interval": "1m" } }
{ "type": "ping" }
```
El servidor responde a `ping` con `{ "type": "pong" }`. Un mensaje mal formado o desconocido recibe:
```json
{ "type": "error", "data": { "code": "invalid_message", "message": "texto legible" } }
```
La conexión sigue abierta tras un error. Si se corta la conexión, el cliente debe reconectar y recibirá un `snapshot` nuevo.

Los precios de los ejemplos son marcadores (0.0), no datos reales.
