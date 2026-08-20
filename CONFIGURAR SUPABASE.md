# Conservar tus datos de forma permanente con Supabase

Guía para que las señales en seguimiento y las operaciones registradas dejen de
perderse cuando la aplicación se reinicia.

**Tiempo estimado:** 15 minutos. **Coste:** gratuito.

---

## Por qué hace falta

Streamlit Community Cloud borra el disco cada vez que la aplicación se
reinicia, se despliega o despierta tras dormirse. Los precios se vuelven a
descargar solos, pero tu seguimiento y tus operaciones no: sólo existen ahí.

Supabase es una base de datos que vive fuera del servidor de la aplicación. Con
ella, tus datos sobreviven a cualquier reinicio.

---

## Paso 1 — Crear la cuenta

Entra en **supabase.com** y regístrate. Puedes usar tu cuenta de GitHub.

---

## Paso 2 — Crear el proyecto

Pulsa **New project** y rellena:

| Campo | Qué poner |
|---|---|
| Name | `qq-quant-os` |
| Database Password | Una contraseña nueva. **Cópiala ahora**: no vuelve a mostrarse |
| Region | La más cercana a ti |
| Plan | Free |

Pulsa **Create new project**. Tarda un par de minutos en prepararse.

---

## Paso 3 — Copiar la cadena de conexión

Dentro del proyecto, arriba a la derecha, pulsa **Connect**.

Busca el apartado **Connection string** y selecciona la pestaña **URI**.

Verás algo así:

```
postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-eu-west-1.pooler.supabase.com:5432/postgres
```

Cópiala y **sustituye `[YOUR-PASSWORD]` por la contraseña del paso 2**,
corchetes incluidos.

> Usa la opción **Session pooler** o **Transaction pooler** si aparecen: son
> más estables para aplicaciones que se conectan de forma intermitente, como
> ésta.

---

## Paso 4 — Configurarla en Streamlit Cloud

1. Ve a **share.streamlit.io** y abre tu aplicación.
2. Pulsa **Manage app**, abajo a la derecha.
3. Los tres puntos (**⋮**) → **Settings** → **Secrets**.
4. Pega esto, con tu cadena real:

```toml
QQ_USER_DATA_URL = "postgresql://postgres.abcdefgh:TU_CONTRASEÑA@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
```

5. Pulsa **Save**. La aplicación se reinicia sola.

---

## Paso 5 — Comprobar que funciona

Abre tu aplicación y ve a **Sistema**. Arriba de la sección de copias debe
aparecer en verde:

> **Base de datos externa conectada.** Tus datos se conservan aunque la
> aplicación se reinicie.

Si sale en rojo, revisa que la contraseña esté bien puesta y que hayas
sustituido los corchetes.

---

## Paso 6 — Verificarlo de verdad

1. Marca una señal en **Señales de hoy**.
2. En **Manage app**, pulsa **Reboot app**.
3. Cuando vuelva a cargar, ve a **Seguimiento**.

Si tu señal sigue ahí, está resuelto.

---

## Para usarlo también en tu ordenador

En PowerShell, una sola vez:

```
[Environment]::SetEnvironmentVariable("QQ_USER_DATA_URL", "tu_cadena_completa", "User")
```

Cierra y abre PowerShell. A partir de ahí, la aplicación local y la publicada
comparten los mismos datos: lo que marques en una aparece en la otra.

---

## Advertencias

**La cadena de conexión es una credencial.** Nunca la subas a GitHub ni la
pegues en una conversación. Va en los secretos de Streamlit y en variables de
entorno, nunca en el código.

**Si se filtra**, cambia la contraseña desde Supabase en
*Settings → Database → Reset database password*, y actualiza la cadena en los
dos sitios.

**El plan gratuito** ofrece 500 MB, muy por encima de lo que ocupan unas
cuantas señales y operaciones. Los proyectos gratuitos se pausan tras una
semana sin actividad; basta con entrar al panel de Supabase para reactivarlo.
