# Plan: calidad conversacional y conocimiento confiable del chatbot

**Objetivo:** mejorar la orientación determinista del chatbot del prototipo GNB, hacer que el feedback corresponda a respuestas reales, conservar contexto de aclaraciones y ampliar la base demo con contenido trazable a páginas oficiales, sin habilitar proveedores de IA ni servicios de pago.

## Límites y decisiones

- Mantener FastAPI, SQLModel, MySQL, Alembic y la arquitectura `router → service → repository → model`.
- No activar OpenAI, n8n, llamadas salientes ni nuevos secretos. La recuperación determinista y el proveedor `Noop` siguen siendo el comportamiento local.
- No presentar este portal académico como canal oficial ni como sustituto de reclamos, bloqueos u operaciones bancarias.
- Los artículos nuevos del seed tendrán URL HTTPS bajo `www.bancognb.com.pe` y fecha de verificación. No se hará scraping ni fetch de esas URLs en tiempo de ejecución.
- El contrato actual permanece retrocompatible. El `message_id` de feedback será opcional para clientes antiguos y permitirá a clientes futuros asociar el voto a la burbuja exacta.
- Las migraciones son aditivas; no modificar conversaciones, tickets ni datos personales existentes.
- No ejecutar el seed operacional completo contra la base local persistente.

## Bloques TDD

### 1. Feedback por respuesta del bot

1. RED: añadir pruebas de feedback para respuesta genérica sin FAQ, FAQ, conversación sin respuesta BOT, voto repetido y autorización anónima/autenticada.
2. GREEN: tabla `chatbot_feedback`, una valoración por `ChatMessage` BOT, upsert idempotente y conservar `FAQFeedback`/métricas FAQ cuando la respuesta está vinculada a FAQ.
3. Añadir métricas agregadas de chat solo para SUPERVISOR; no exponer votos ni identidades de forma pública.
4. Documentar respuesta 409/422 y política de minimización de datos.

### 2. Contexto de turnos y selección de aclaraciones

1. RED: probar selección de una opción por texto, ordinal, respuesta breve contextual, cambio explícito de tema, límites de intentos y reset.
2. GREEN: persistir IDs de FAQ candidatos para las opciones de aclaración; resolver opción solo contra candidatos publicados/activos; conservar hasta dos entradas breves de contexto únicamente mientras exista aclaración pendiente.
3. Preferir la intención explícita del mensaje nuevo; usar contexto solo para respuestas cortas/continuaciones y solo si mejora el match. Borrar el estado pendiente al resolver, cambiar claramente de tema, resetear o convertir.
4. Mantener aclaración antes de ofrecer ticket. No inferir intención por inspección de frases de respuesta generadas.

### 3. Procedencia y catálogo de conocimiento

1. RED: probar validación HTTPS/host oficial, serialización de fuente, snapshots de procedencia y catálogo seed mínimo con cobertura por tema.
2. GREEN: agregar `source_url` y `source_verified_at` a FAQ y a versiones históricas, validar que los enlaces sean HTTPS del dominio oficial permitido y extender el contexto RAG con URL para citas verificables.
3. Ampliar FAQs demo con respuestas prudentes sobre canales digitales, cuentas, tarjetas, créditos y reclamos; cada dato variable deberá remitir a fuente oficial y advertir que la información vigente está en el canal oficial.
4. Crear seed dedicado de conocimiento que solo inserte/actualice FAQs y versiones; no tocar tickets, conversaciones ni usuarios.
5. Crear guía de gobierno editorial: alcance, revisión de vigencia, prohibiciones de seguridad, límites del prototipo, métricas y rutina de mantenimiento.

### 4. Revisión y entrega

1. Ejecutar suite enfocada primero; luego suite completa, Ruff, formato y `git diff --check`.
2. Revisar migración en base desechable y ejecutar `alembic check`.
3. Aplicar migración al backend local y reconstruir/recrear solo `api` con `docker compose up -d --no-deps --build api`; no recrear ni ejecutar `seed`/`migrate` como servicios del Compose.
4. Probar `/health/ready`, `/docs`, OpenAPI, FAQ con fuentes, feedback anónimo/autenticado, permisos de métricas y conversación de seguimiento.
5. Inspeccionar diff/status; conservar sin cambios los archivos preexistentes no rastreados de `docs/`, `output/` y `outputs/`.
6. Un commit focalizado en `main`, `git push origin main` sin force push; verificar el SHA remoto y estado final.

## Riesgos y mitigaciones

- **Contenido mutable:** persistir URL y fecha de verificación; evitar tarifas, tasas y promesas de aprobación; revisar antes de publicar.
- **Enlaces maliciosos/SSRF:** no descargar fuentes; admitir solo HTTPS y hostname oficial exacto; la URL se devuelve como referencia, no se solicita desde el servidor.
- **Contexto arrastrado:** candidato permitido, tamaño/turnos acotados y prioridad al tema explícito; el reset limpia el contexto.
- **Datos de feedback:** usar IDs de conversación/mensaje y rol, sin recopilar datos bancarios; índices y unicidad evitan votos duplicados.
- **Compatibilidad frontend:** conservar campos existentes y agregar opcionales. Las opciones de aclaración actuales siguen siendo texto; no requiere cambios inmediatos del frontend.
- **Datos locales:** no correr el seed operacional que puede reescribir tickets de demo.
