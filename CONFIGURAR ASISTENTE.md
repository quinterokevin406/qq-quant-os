# Configurar el asistente

El asistente necesita una clave de API de Anthropic. Sin ella, el panel
funciona igual pero el asistente aparece desactivado.

## Paso 1 — Conseguir la clave

1. Entra en **console.anthropic.com** y crea una cuenta.
2. Ve a **Settings → API Keys → Create Key**.
3. Cópiala. Empieza por `sk-ant-`.
4. **No la escribas en ningún chat ni la enseñes en capturas.**

## Paso 2 — En tu equipo

En PowerShell, sustituyendo `TU_CLAVE`:

```
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "TU_CLAVE", "User")
```

Cierra PowerShell y ábrelo de nuevo.

## Paso 3 — En Streamlit Cloud

**Manage app → ⋮ → Settings → Secrets**. Añade una línea más, sin borrar las
que ya hay:

```
ANTHROPIC_API_KEY = "TU_CLAVE"
```

Guarda y espera un minuto.

## Lo que cuesta

Cada pregunta se factura por uso. Una consulta típica cuesta del orden de
céntimos, pero **el panel es público**: cualquiera con el enlace puede
preguntar y gastar tu saldo.

Recomendaciones:

- Pon un **límite de gasto mensual** en console.anthropic.com, en Billing.
- Si el panel va a ser público, considera hacerlo privado desde
  **Manage app → Settings → Sharing**.

## Qué puede y qué no puede responder

**Puede:** qué señales hay hoy, cómo van tus operaciones en seguimiento, qué
dice el motor de reparto, qué instrumentos y estrategias existen, qué
limitaciones tiene cada cifra.

**No puede:** predecir precios, ni responder sobre nada que no esté en el
panel. Si le preguntas algo que no ve, lo dirá en lugar de inventarlo.

Esa restricción es deliberada. Un asistente que improvisa cifras es más
peligroso que una tabla equivocada: una frase amable se lee como un consejo
fundado, y una tabla se lee con desconfianza.
