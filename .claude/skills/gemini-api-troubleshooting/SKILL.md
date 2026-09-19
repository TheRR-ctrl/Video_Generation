---
name: gemini-api-troubleshooting
description: Checklist de diagnóstico para cuando el pipeline (video_generation o video-scout-pipeline) falla con errores de la API de Gemini como 401 UNAUTHENTICATED, ACCESS_TOKEN_TYPE_UNSUPPORTED, API_KEY_INVALID, API_KEY_SERVICE_BLOCKED, 429 RESOURCE_EXHAUSTED, o "prepayment credits are depleted". Úsala SIEMPRE que un run de GitHub Actions falle en la etapa de Gemini (plan/guion/HyperFrames/TTS), antes de asumir que es cuota, facturación, o un bug de código — la mayoría de estas fallas en este proyecto resultaron ser errores de configuración triviales (repo equivocado, restricción de API, formato de key) que se diagnostican en minutos con los pasos de acá, no en horas de prueba y error.
---

# Diagnóstico de errores de la API de Gemini

Contexto: en una sesión real de este proyecto, un solo problema de autenticación tomó **más de 3 horas** de troubleshooting porque se probaron soluciones caras (activar facturación, cargar saldo prepago, migrar a Vertex AI) antes de aislar la causa real con una prueba simple. La causa raíz terminó siendo que el secret `GEMINI_API_KEY` se estaba actualizando en el **repositorio de GitHub equivocado** (un repo hermano con nombre parecido). Esta skill existe para que la próxima vez el diagnóstico tome minutos, no horas.

## Regla de oro: aislar antes de reintentar

Antes de tocar código, config, facturación o de relanzar el pipeline "a ver si esta vez sí", **probá la key directamente con curl, fuera del pipeline y del SDK**:

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key=LA_KEY_AQUI" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"di hola"}]}]}'
```

- **Si esto funciona pero el pipeline sigue fallando** → el problema NO es la key ni la cuenta de Google. Es algo del lado del repo/secret/código (ver "Repo equivocado" abajo — es la causa más común).
- **Si esto también falla** → el problema es la key/cuenta/proyecto de Google. Seguí el árbol de diagnóstico según el mensaje de error exacto.

Esta única prueba ahorra la mayor parte del tiempo perdido: separa "problema de Google" de "problema nuestro" en un solo comando, antes de gastar tiempo en facturación, saldo, o código que no tenía nada que ver.

## Árbol de diagnóstico por mensaje de error

### `401 UNAUTHENTICATED` con `reason: ACCESS_TOKEN_TYPE_UNSUPPORTED`

Mensaje completo típico: *"Expected OAuth 2 access token, login cookie or other valid authentication credential."*

Dos causas posibles, en este orden de probabilidad:

1. **Bug de las keys `AQ.` de AI Studio** (confirmado activo entre ago-sept 2026): Google migró la emisión de keys en `aistudio.google.com/apikey` de un formato viejo (`AIzaSy...`, "Traffic Key") a uno nuevo (`AQ.`, "Authentication Key"). El backend de `generativelanguage.googleapis.com` no siempre acepta el formato nuevo, incluso con la key correctamente vinculada a una cuenta de servicio. Sintoma: **toda key nueva creada desde AI Studio para esta cuenta empieza con `AQ.` y falla así, sin excepción, sin importar cuántas veces la regeneres.**
   - Workaround: crear la key manualmente desde **Google Cloud Console → APIs y servicios → Credenciales → Crear credenciales → Clave de API** (no desde AI Studio). Esto puede dar una key `AIzaSy...` que si funciona.
   - Si "Gemini API" aparece deshabilitada/gris en el selector de restricciones de esa key nueva, es porque tu organización de Cloud exige que las keys restringidas a Gemini estén vinculadas a una cuenta de servicio — es un callejón sin salida por esta vía (ver más abajo).
   - Reportar bugs nuevos de esto en el foro oficial: `discuss.ai.google.dev` (el formulario dedicado de Google para esto históricamente se cierra diciendo que ya "arreglaron la mayoría", pero eso no significa que tu cuenta específica esté arreglada — probá igual, no asumas).

2. **La key es válida pero está en modo incorrecto para el SDK** — mucho menos común, pero si el curl directo funciona y el pipeline sigue con este error exacto, no es esto: andá directo a "Repo equivocado".

### `API_KEY_INVALID` (código 400)

La key literalmente no es reconocida. Antes de sospechar nada raro, descartá lo obvio:
- ¿Copiaste la key completa, sin cortar el principio o el final?
- ¿Esperaste 1-2 minutos desde que la creaste? Las keys nuevas a veces tardan en propagarse.
- ¿Es realmente una key de "Gemini"/Generative Language, no de otro producto (Maps, YouTube, etc.)?

### `API_KEY_SERVICE_BLOCKED` (código 403)

**Esto es un error de configuración, no de facturación ni de cuenta.** Significa: la key existe y es válida, pero tiene una lista de "Restricciones de API" que no incluye la API que estás llamando.

Arreglo: Google Cloud Console → Credenciales → clic en la key → sección **"Restricciones de API"** → agregar **"Generative Language API"** a la lista (o cambiar a "No restringir clave"). Guardar y esperar ~1-3 min.

Si "Gemini API" aparece **deshabilitada/gris** en ese selector (tanto al editar como al crear una key nueva), es porque la organización de Google Cloud exige que las keys con acceso a Gemini estén vinculadas a una cuenta de servicio, y esa vinculación solo se puede hacer vía AI Studio (que en este período emite keys `AQ.` rotas — ver arriba). Es un callejón sin salida circular; en ese caso la solución de fondo es autenticar con la cuenta de servicio directamente (Vertex AI / credencial JSON), no con una "API key".

### `429 RESOURCE_EXHAUSTED` (quota)

Dos variantes con textos parecidos pero significado distinto — leé el mensaje completo:

- `quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier` → tier gratuito agotado (20 req/día para `gemini-3.6-flash`). Solución real: activar facturación (ver siguiente sección). Solución temporal: esperar — la cuota se recupera lentamente a lo largo del día, no de golpe a medianoche.
- `Your prepayment credits are depleted` → facturación activa pero saldo prepago en $0. Cargar saldo en `aistudio.google.com/billing` (NO en la consola de Cloud Billing general — ese es un sistema de saldo separado, ver abajo).

### `403 PERMISSION_DENIED` con `Lightning dunning decision is deny for project: projects/<numero>`

**Es facturación, no configuración ni código.** "Dunning" es el término de cobranzas:
Google marcó el proyecto como moroso y le cortó el acceso a la API. No se arregla
regenerando la key, ni cambiando restricciones, ni tocando el repo — la key es
válida y el proyecto existe; lo que está denegado es el proyecto entero.

Cómo se distingue de los otros 403: `API_KEY_SERVICE_BLOCKED` culpa a la *key*
(restricciones de API), este culpa al *proyecto* y nombra su número. Y a
diferencia de un 429, no se recupera esperando: no es un tope de uso, es una
decisión de cobranza que sigue vigente hasta que se salda.

Bloquea TODO el proyecto de una: el modelo de texto (plan, guion, HyperFrames),
el TTS de Gemini y Veo. Si en la misma sesión viste 429 en Veo poco antes, no lo
leas como cuota agotada por uso: el mensaje "check your plan and billing details"
sin `quotaId` puede ser el mismo problema de cobranza asomando primero por ahí.

Qué mirar, en este orden (todo del lado del usuario, no hay arreglo por código):
1. `payments.google.com` → ¿hay un pago rechazado o un método de pago vencido?
   Es la causa más frecuente: la tarjeta falló y el saldo que la UI muestra
   acreditado no llegó a cobrarse.
2. `aistudio.google.com/billing` → ¿el saldo prepago está en cero o con aviso de
   "saldo pendiente"?
3. `console.cloud.google.com/billing` → ¿la cuenta de facturación del proyecto
   `<numero>` sigue vinculada y activa?

Mientras tanto el pipeline no puede correr ninguna etapa que use Gemini, así que
no tiene sentido relanzarlo "a ver si esta vez sí": va a fallar igual, y si se
lanza con `regenerar_guion`/`regenerar_plan` encima **borra el guion y los clips
cacheados antes de descubrir que no puede reescribirlos** (pasó en la corrida
34003737710). Si hay que relanzar para probar otra cosa, hacelo sin esos flags.

### Facturación: modelo de "prepago", no de "pago por uso" automático

`aistudio.google.com/billing` usa un sistema de **crédito prepago** (mínimo de compra variable según moneda/región — en México fue MXN 500, no los $5 USD que documenta Google para cuentas en USD): comprás saldo por adelantado, se descuenta con el uso, se corta el servicio a $0. Esto es **distinto** de:
- La consola general de Cloud Billing (`console.cloud.google.com/billing`) — ahí se ve el gasto histórico y se gestiona el método de pago, pero **no se carga el saldo prepago de Gemini ahí**.
- Los $300 USD de crédito de bienvenida de Google Cloud — **excluidos explícitamente del uso en la API de Gemini desde marzo 2026** (antes de esa fecha sí aplicaban). Si tu cuenta es nueva, ese crédito no te sirve acá, tenés que cargar prepago aparte.
- Vertex AI — factura directo a Cloud Billing por uso, sin sistema de prepago; es un producto y una vía de autenticación totalmente distintos (cuenta de servicio, no API key).

Si ves un aviso de **"saldo pendiente, realiza un pago para restablecer el servicio"** con un crédito ya cargado visible, es porque el *cargo real* a la tarjeta falló (tarjeta rechazada) aunque el saldo aparezca acreditado en la UI — revisar el método de pago en `payments.google.com`, no asumir que ya está resuelto solo porque la UI muestra el saldo.

## Repo equivocado: la causa más común y más tonta

**Antes de sospechar de Google, confirmá que estás editando el secret en el repositorio correcto.** Este proyecto tiene un repo hermano casi idéntico en propósito y convención de nombres (`video_generation` vs `video-scout-pipeline`). Es trivialmente fácil terminar en la pestaña del navegador equivocada después de tener varias abiertas.

Chequeo de 5 segundos: mirá la URL antes de tocar cualquier secret.
```
github.com/<owner>/<REPO>/settings/secrets/actions/GEMINI_API_KEY
                    ^^^^ ¿es "Video_Generation" o "video-scout-pipeline"?
```

Señal de alerta de que este es el problema: la key probada por curl funciona perfecto, pero el pipeline sigue fallando con el mismo error de siempre después de "actualizar" el secret repetidas veces. Si eso pasa, es prácticamente seguro que el secret se está guardando en el repo que no corresponde.

## Orden de diagnóstico recomendado (resumen)

1. `curl` directo con la key — ¿funciona?
2. Si sí funciona pero el pipeline falla → confirmar el repo de GitHub antes que nada.
3. Si no funciona → leer el `reason`/`status` exacto del error y seguir el árbol de arriba.
4. Nunca activar facturación, cargar saldo, ni migrar de motor/API como primer paso — son soluciones caras y lentas para un problema que casi siempre es de configuración.
