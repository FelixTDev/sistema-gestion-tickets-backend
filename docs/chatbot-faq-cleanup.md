# Limpieza de contenido de FAQ

La revisión `0020_faq_content_cleanup` actualiza el catálogo FAQ curado que se
publicaba con respuestas, fuentes y metadatos específicos de una entidad. No
agrega cambios de esquema.

## Filas objetivo

La migración busca por el texto de pregunta del catálogo anterior definido en
`_FAQ_CONTENT` de
[`0020_faq_content_cleanup.py`](../migrations/versions/0020_faq_content_cleanup.py).
También acepta la pregunta nueva para cubrir una actualización parcial previa
del seed. Los IDs se resuelven en la base al ejecutar la migración; no están
fijados en el repositorio ni se leyeron desde producción.

Se actualizan las filas existentes que correspondan a estas 19 preguntas y, si
existe, a la fila alias antigua de requisitos de tarjeta:

| Categoría | Pregunta que queda en el catálogo |
| --- | --- |
| Tarjetas | ¿Qué requisitos necesito para solicitar una tarjeta? |
| Banca digital | ¿Cómo ingreso a la banca digital? |
| Cuentas | ¿Cómo consulto el saldo de mi cuenta? |
| Tarjetas | ¿Qué hago si mi tarjeta está bloqueada? |
| Créditos | ¿Cómo solicito información sobre un crédito? |
| Reclamos simples | ¿Cómo consulto el estado de un reclamo? |
| Cuentas | ¿Cómo solicito un estado de cuenta? |
| Solicitudes de información | ¿Cómo puedo contactar a un asesor? |
| Tarjetas | ¿Puedo habilitar o deshabilitar compras por internet en mi tarjeta? |
| Banca digital | ¿Cómo me afilio a Banca Móvil? |
| Banca digital | ¿Qué hago si olvidé mi contraseña de Banca Móvil? |
| Cuentas | ¿Qué necesito para abrir una Cuenta de Ahorro? |
| Cuentas | ¿Qué tipos de cuentas ofrece una entidad financiera? |
| Créditos | ¿El asistente puede decirme si me aprobarán un préstamo? |
| Reclamos simples | ¿Cuál es la diferencia entre un reclamo y un requerimiento? |
| Reclamos simples | ¿Cómo presento un reclamo formal ante una entidad financiera? |
| Reclamos simples | ¿Cuánto tarda la respuesta de un reclamo formal? |
| Solicitudes de información | ¿Cuál es el horario de Banca por Teléfono? |
| Tarjetas | ¿Qué hago si no reconozco una operación con mi tarjeta? |

El alias antiguo se resuelve a la fila de requisitos de tarjeta. La migración
resuelve los IDs existentes al aplicar estas preguntas; no fija IDs ni crea,
borra o fusiona FAQ.

La migración conserva cada ID, la categoría, el estado y las relaciones. Limpia
el texto actual de la FAQ y todos sus snapshots asociados en `faq_versions`,
elimina las fuentes y sus fechas de verificación, y deja respuestas generales
cuando el catálogo anterior contenía datos específicos sin respaldo neutral.
El downgrade no restaura esos datos retirados.

El seed también actualiza por pregunta las FAQ que ya encuentra. En despliegues
existentes la migración es necesaria porque el seed no se ejecuta al iniciar el
backend; el bootstrap de producción solo atiende la creación inicial de una
cuenta.
