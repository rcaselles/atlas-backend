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

## Secretos

Este repositorio es público. Toda la configuración va por variables de entorno (ver `.env.example`); nunca se suben claves.
