# Instrucciones del backend

## Contexto

Este backend implementa un prototipo académico de gestión de tickets con chatbot para Banco GNB Perú. No es un sistema oficial ni procesa operaciones bancarias reales.

## Stack

- Python 3.
- FastAPI.
- Pydantic.
- SQLModel.
- MySQL.
- Alembic.
- Pytest.

## Arquitectura obligatoria

Organizar el código primero por módulo de dominio y luego por responsabilidad:

```text
app/modules/<entidad>/
├── models/
├── schemas/
├── repositories/
├── services/
└── api/
```

Usar dentro de cada módulo la separación:

```text
router/controller → service → repository → model
```

- Los routers manejan HTTP y validación de entrada.
- Los servicios contienen reglas de negocio.
- Los repositorios manejan persistencia.
- Los modelos representan tablas.
- Los esquemas definen contratos públicos.

No crear carpetas globales `app/models`, `app/services` o `app/repositories` para lógica de negocio. El código transversal realmente reutilizable puede vivir en `app/shared`.

## Reglas de implementación

- Usar `Annotated` y dependencias de FastAPI.
- Definir tipos de retorno o `response_model`.
- No usar `RootModel` ni `...` como marcador de campos obligatorios.
- No colocar consultas SQL directamente en los routers.
- No exponer contraseñas, hashes, tokens ni datos sensibles.
- Registrar historial en cada mutación importante de tickets.
- Mantener rutas bajo `/api/v1`.
- Escribir pruebas para las reglas de negocio y permisos.
- Actualizar las migraciones cuando cambie el modelo.

## Comandos mínimos

```bash
fastapi dev
pytest
alembic upgrade head
```

Antes de finalizar un cambio, ejecutar pruebas, linting y revisión de migraciones.
