# Backend de gestión de tickets

Prototipo académico independiente para gestión de tickets. No se conecta a sistemas del Banco GNB Perú ni procesa operaciones bancarias reales.

## Desarrollo local

1. Copiar `.env.example` a `.env` y ajustar solo valores locales.
2. Crear un entorno virtual e instalar dependencias: `pip install -e ".[dev]"`.
3. Iniciar MySQL: `docker compose up -d db`.
4. Aplicar migraciones: `alembic upgrade head`.
5. Cargar datos demo: `python -m app.seed.demo_data`.
6. Iniciar API: `fastapi dev app/main.py`.

Swagger queda disponible en `http://localhost:8000/docs` y el health check en `/api/v1/health`.

## Pruebas y calidad

```bash
pytest
ruff check .
ruff format --check .
```

Las cuentas demo se crean mediante seed idempotente. Sus contraseñas se leen desde variables de entorno (`DEMO_*_PASSWORD`) y tienen valores locales únicamente para desarrollo; no son credenciales reales.
