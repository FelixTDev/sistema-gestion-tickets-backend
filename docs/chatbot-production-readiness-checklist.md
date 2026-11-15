# Checklist para habilitar el chatbot con IA en producción

Este checklist no autoriza una activación en producción. La IA está deshabilitada
por defecto y la evaluación de esta demo usa solo datos sintéticos, SQLite
temporal, `gpt-5.4-mini`, embeddings apagados y hasta 128 tokens de salida.

> **No usar con datos reales de clientes.** La corrida permitida en esta línea
> usa un incentivo sujeto a compartir entradas y salidas con OpenAI. Las
> preguntas y respuestas enviadas a ese proveedor no son apropiadas para
> información privada, financiera o identificable de clientes. Mantener
> `CHATBOT_AI_ENABLED=false` para cualquier entorno que contenga esos datos.

## Variables y secretos

Configurar en el archivo privado `.env.production` del VPS, nunca en Git,
imágenes, historial de comandos ni logs:

| Variable | Valor inicial recomendado | Nota |
| --- | --- | --- |
| `CHATBOT_AI_ENABLED` | `true` durante el canary; `false` hasta autorizarlo | `false` conserva el fallback determinístico. |
| `CHATBOT_AI_PROVIDER` | `openai` | El proveedor seleccionado por el backend. |
| `CHATBOT_AI_MODEL` | `gpt-5.4-mini` | Modelo utilizado en la batería real. |
| `OPENAI_API_KEY` | Secreto de proyecto | Clave del proyecto OpenAI con acceso a Responses API. |
| `CHATBOT_AI_EMBEDDINGS_ENABLED` | `false` al inicio | Coincide con la configuración probada; habilitar embeddings requiere una validación y presupuesto aparte. |
| `CHATBOT_AI_DAILY_BUDGET_USD` | `0.50` | Tope interno del backend; configurar además límites y alertas en OpenAI. |
| `CHATBOT_AI_MAX_OUTPUT_TOKENS` | `128` | Límite aplicado por esta línea de evaluación; no aumentarlo para la demo. |
| `CHATBOT_AI_TIMEOUT_SECONDS` | `15` | El backend debe responder con fallback ante timeout. |
| `CHATBOT_AI_REQUESTS_PER_MINUTE` | `5` | Límite global del backend. |
| `CHATBOT_AI_MAX_RESPONSES_PER_CONVERSATION` | `3` | Evita llamadas ilimitadas dentro de una conversación. |
| `CHATBOT_AI_MAX_PROVIDER_CALLS_PER_ACTOR_PER_HOUR` | `20` | Límite por actor. |

`deploy/production/compose.yml` pasa estas variables al contenedor. El archivo
`.env.production.example` documenta los valores sin incluir credenciales.
Conservar `CHATBOT_AI_ENABLED=false` y `CHATBOT_AI_PROVIDER=noop` en el perfil
inicial hasta que el operador apruebe el canary.

## Previo al despliegue

- [ ] Revisar los commits aprobados de backend y frontend; confirmar que no hay
      cambios locales sin revisar.
- [ ] Guardar la clave en el almacén/archivo privado del VPS con permisos
      restrictivos. No pegarla en SSH, terminales compartidas ni tickets.
- [ ] Configurar presupuesto, alertas y acceso del proyecto OpenAI; el límite
      interno del backend no sustituye el límite del proveedor.
- [ ] Verificar acceso SSH, espacio disponible y estado de los servicios.
- [ ] Crear un respaldo lógico de MySQL y comprobar que puede restaurarse en un
      entorno aislado.
- [ ] Confirmar la versión actual con `alembic current` y la cabeza esperada con
      `alembic heads`: `0019_chatbot_quality_sources`.
- [ ] No ejecutar seeds de demostración ni el Compose de desarrollo en el VPS.

## Despliegue y canary

1. Desplegar la versión revisada manteniendo IA desactivada.
2. Confirmar `/api/v1/health/ready`, login, consulta de tickets y flujo normal de
   tickets con la API lista.
3. Aplicar `alembic upgrade head` después del respaldo. La cabeza esperada es
   `0019_chatbot_quality_sources`; esta migración agrega procedencia de fuentes
   y la tabla de feedback.
4. Activar `CHATBOT_AI_ENABLED=true` y `CHATBOT_AI_PROVIDER=openai` en el archivo
   privado; recrear únicamente el servicio API según el procedimiento de
   `deploy/production/README.md`.
5. Hacer un canary manual con una FAQ publicada y verificada. Confirmar
   `response_source=AI`, `fallback_reason=null`, fuente visible y URL válida.
6. Probar una pregunta fuera de contexto, un intento de prompt injection, una
   advertencia de credenciales y una consulta privada. Los datos privados deben
   quedarse en el backend y nunca llegar al proveedor.
7. Aumentar el tráfico gradualmente solo después de revisar los resultados y el
   gasto observado en OpenAI.

La evaluación local con escenarios sintéticos y SQLite temporal valida el
backend aislado, no sustituye un estudio con usuarios ni un proceso de
aprobación de seguridad. Los resultados y denominadores reales se mantienen en
`docs/chatbot-evaluation.md` y `docs/evaluation/chatbot_real_evaluation.json`.

## Rollback

1. En `.env.production`, establecer `CHATBOT_AI_ENABLED=false` y
   `CHATBOT_AI_PROVIDER=noop`; quitar la clave del entorno del contenedor si ya
   no se requiere.
2. Recrear solo el servicio API y verificar el endpoint de readiness y el
   fallback determinístico del chatbot.
3. Si hay regresión de aplicación, volver a la imagen/commit anterior compatible
   con la base actual.
4. No ejecutar `alembic downgrade 0019_chatbot_quality_sources` como rollback
   rutinario: elimina la tabla de feedback y los campos de procedencia. La
   reversión de esquema requiere una decisión de recuperación separada y un
   respaldo verificado.

## Monitoreo y criterios de parada

Durante el canary, revisar:

- `response_source`, `fallback_reason` y `validation_subrule` en las trazas;
- respuestas `OUT_OF_CONTEXT`, proveedores con timeout/429/401 y circuito
  abierto;
- porcentaje de respuestas IA aceptadas, fuentes y URLs devueltas;
- latencia, tokens, gasto y presupuesto diario del backend y de OpenAI;
- errores de consultas privadas y confirmación de que no se envían al proveedor.

Desactivar IA si hay filtración de datos privados, fuentes incorrectas,
afirmaciones no sustentadas, tasa anómala de fallbacks/errores o gasto por
encima del límite acordado. Mantener el fallback determinístico disponible.
