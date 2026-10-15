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

## Secretos

Este repositorio es público. Toda la configuración va por variables de entorno (ver `.env.example`); nunca se suben claves.
