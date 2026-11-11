# Probar OpenAI localmente

El backend incluye un adaptador para la Responses API. La IA permanece apagada
por defecto; no se envían solicitudes a OpenAI hasta habilitarla expresamente.

## Configuración local

1. Copia `.env.example` a `.env` si todavía no tienes un `.env` local.
2. En `.env`, configura:

   ```dotenv
   CHATBOT_AI_ENABLED=true
   CHATBOT_AI_PROVIDER=openai
   CHATBOT_AI_MODEL=gpt-5.4-mini
   OPENAI_API_KEY=<tu-clave-secreta>
   ```

3. Mantén `CHATBOT_AI_MAX_OUTPUT_TOKENS=384`, `CHATBOT_AI_MAX_RESPONSES_PER_CONVERSATION=3`,
   `CHATBOT_AI_REQUESTS_PER_MINUTE=5` y `CHATBOT_AI_MAX_PROVIDER_CALLS_PER_ACTOR_PER_HOUR=20`
   durante la prueba inicial. El último límite se comparte entre conversaciones del mismo
   usuario autenticado; para visitas anónimas se agrupa por la clave derivada de la IP.
4. Reconstruye y recrea solo el servicio `api` para que Compose aplique las variables.
   No es necesario reiniciar MySQL ni ejecutar migraciones.
5. Prueba con preguntas sintéticas que tengan una FAQ oficial vigente y revisa
   **Usage/Costs** en la plataforma de OpenAI. El RAG puede responder de forma
   determinística o usar fallback sin hacer una llamada al modelo.

No subas `.env`, no incluyas la clave en el frontend ni la pegues en chats o logs.
El adaptador usa `store=false` y no envía historial de conversaciones completo;
envía únicamente el mensaje minimizado y las fuentes FAQ seleccionadas. Esto evita
guardar el estado de respuesta de la API, pero no equivale a Zero Data Retention:
consulta las [reglas vigentes de retención de datos](https://developers.openai.com/api/docs/guides/your-data).

Las pruebas automatizadas interceptan la llamada HTTP y no consumen saldo. El test
real contra OpenAI debe ejecutarse manualmente después de guardar la clave local.
