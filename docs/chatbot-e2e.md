# E2E local del chatbot

El flujo de Playwright comprueba la integración entre el navegador, el API y una
base SQLite desechable. Usa únicamente FAQ públicas verificadas y tickets
sintéticos; no necesita OpenAI ni ejecuta migraciones. Por defecto usa un
proveedor falso de red que solo devuelve contenido de FAQ pública ya verificada.
`npm run e2e:fallback` repite el mismo recorrido con IA deshabilitada y el
proveedor Noop. El API y Vite usan puertos locales efímeros y se inician desde un
directorio temporal; el runner comprueba que ese directorio no contenga `.env`
sin abrir el archivo. Al terminar, detiene ambos procesos y elimina solo el
directorio temporal de la corrida.

## Preparación y ejecución

Requisitos: dependencias instaladas en el frontend (`npm ci`), el entorno Python
del backend (`backend\.venv`) y Chromium de Playwright. Si Chromium aún no está
instalado, desde `frontend` ejecutar una vez:

```powershell
npm run e2e:install
```

Cada corrida se ejecuta desde `frontend`:

```powershell
npm run e2e
npm run e2e:fallback
npm run e2e:visual
```

`npm run e2e:visual` construye la versión de producción, ejecuta el mismo flujo
en Vite de desarrollo y en `vite preview`, y compara estilos calculados de la
página, el widget y el diálogo. Ambos recorridos usan SQLite temporal y proveedor
falso.

El script usa el `backend\.venv` local y crea una SQLite con nombre aleatorio
bajo el directorio temporal del sistema. El usuario de prueba es
`cliente@demo.com`, `asesor@demo.com` y `supervisor@demo.com`; sus claves
aleatorias se generan en memoria para cada corrida y solo se usan contra esa
base temporal. No se conecta a MySQL, no lee `.env` y configura `APP_ENV=development`,
`CHATBOT_AI_ENABLED=true` únicamente en la variante con proveedor falso y
`CHATBOT_AI_ENABLED=false` en la variante de fallback; ambas usan claves vacías,
`CHATBOT_AI_EMBEDDINGS_ENABLED=false`, correo `noop` y SQLite temporal.

## Comprobaciones cubiertas

- Bienvenida del widget, CTA del menú, selección de categoría y subcategoría,
  navegación hacia atrás, vuelta al menú y restauración de estado tras recargar.
- Fuentes FAQ limitadas a URLs oficiales verificadas y respuesta de una consulta
  pública basada en el catálogo, una vez con respuesta del proveedor falso y una
  vez en modo determinístico.
- Comentario no útil sin escalamiento prematuro; saldo, aritmética e inyección
  atendidos por ruta determinística sin fuentes ni proveedor IA.
- Inicio de sesión del cliente, asociación de la conversación anónima, avisos
  proactivos propios y confirmación de resolución.
- Borrador
  de asesor, pausa al mostrar el menú, falta de reanudación ante un saludo y
  reanudación mediante la opción explícita.
- Formulario web, confirmación explícita y conversión a ticket; consulta
  autenticada de tickets propios y cierre persistido que rechaza mensajes
  posteriores.
- Visibilidad del ticket sin asignar en la cola operativa y asignación mediante
  el panel de supervisión antes de abrir el detalle del asesor. El E2E comprueba
  el resumen determinístico dentro del ticket asignado.

La E2E es una comprobación funcional reproducible, no una prueba estadística del
modelo ni de comportamiento con usuarios. Las métricas de intención, RAG,
grounding, seguridad, latencia y consumo se deben reportar con sus denominadores
y separar los casos determinísticos de los que permiten clasificación IA. La
corrida E2E apaga IA por diseño y no aporta medición probabilística, latencia de
proveedor ni costo.
