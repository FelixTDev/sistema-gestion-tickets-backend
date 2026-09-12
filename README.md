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

## Autenticación

El registro crea únicamente usuarios con rol `CLIENTE`. El login devuelve un access token JWT para enviarlo como `Authorization: Bearer <access_token>` en `/api/v1/auth/me` y en futuras rutas protegidas. El logout es lógico y confirma el cierre de sesión del cliente; los JWT son stateless y expiran según `ACCESS_TOKEN_EXPIRE_MINUTES`.

Las cuentas demo usan las variables `DEMO_CLIENT_PASSWORD`, `DEMO_ADVISOR_PASSWORD` y `DEMO_SUPERVISOR_PASSWORD`. Si no se definen en desarrollo, el seed usa `demo-password-local`.

## Pruebas y calidad

```bash
pytest
ruff check .
ruff format --check .
alembic check
```

Las cuentas demo se crean mediante seed idempotente. Sus contraseñas se leen desde variables de entorno (`DEMO_*_PASSWORD`) y tienen valores locales únicamente para desarrollo; no son credenciales reales.

## Chatbot V1 y base de conocimiento

El chatbot responde exclusivamente con FAQ activas y palabras clave normalizadas
(mayúsculas, minúsculas y acentos). No utiliza LLM, RAG ni servicios externos de
inteligencia artificial. Las conversaciones pueden iniciarse sin autenticación y
asociarse después a un cliente autenticado. Una consulta sin coincidencia se marca
como no resuelta y ofrece continuar con un ticket en una fase posterior; esta fase
no crea tickets ni solicita datos bancarios sensibles.

Endpoints disponibles:

- `GET /api/v1/faqs` y `GET /api/v1/faqs/{faq_id}`: consultar FAQ activas.
- `POST /api/v1/chat/conversations`: iniciar una conversación anónima o autenticada.
- `GET /api/v1/chat/conversations/{conversation_id}`: consultar una conversación autorizada.
- `POST /api/v1/chat/conversations/{conversation_id}/messages`: guardar el mensaje y obtener la respuesta FAQ del bot.
- `POST /api/v1/chat/conversations/{conversation_id}/link-user`: asociar una conversación anónima al cliente autenticado.

Ejemplo de uso:

```bash
# Conversación anónima
curl -X POST http://localhost:8000/api/v1/chat/conversations

# Mensaje FAQ
curl -X POST http://localhost:8000/api/v1/chat/conversations/<conversation_id>/messages \
  -H "Content-Type: application/json" \
  -d '{"content":"¿Qué requisitos necesito para una tarjeta?"}'

# Login y asociación de una conversación
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"cliente@demo.com","password":"demo-password-local"}'
curl -X POST http://localhost:8000/api/v1/chat/conversations/<conversation_id>/link-user \
  -H "Authorization: Bearer <access_token>"
```
