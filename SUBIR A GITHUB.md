# Publicar el panel en internet

Guía para poner QQ Quant OS accesible desde cualquier ordenador.

---

## Parte 1 — Subir el código a GitHub

Todos los comandos van en **PowerShell**, dentro de `C:\dev\qq-quant-os`.

### 1. Configurar Git (sólo la primera vez)

```
git config --global user.name "Kevin Quintero"
git config --global user.email "quinterokevin406@gmail.com"
```

### 2. Preparar el repositorio

```
git init
```

```
git add .
```

```
git commit -m "QQ Quant OS - plataforma de investigacion cuantitativa"
```

### 3. Conectar con GitHub y subir

```
git branch -M main
```

```
git remote add origin https://github.com/quinterokevin406/qq-quant-os.git
```

```
git push -u origin main
```

Se abrirá una ventana pidiendo iniciar sesión en GitHub. Autoriza con el
navegador.

Al terminar, recarga la página del repositorio: deben aparecer todas las
carpetas.

---

## Parte 2 — Publicar el panel

### 1. Crear la cuenta

Ve a **share.streamlit.io** y entra con **Continue with GitHub**. Autoriza el
acceso cuando lo pida.

### 2. Desplegar

Clic en **Create app** y luego en **Deploy a public app from GitHub**.

Rellena:

| Campo | Valor |
|---|---|
| Repository | `quinterokevin406/qq-quant-os` |
| Branch | `main` |
| Main file path | `dashboard/app.py` |

Clic en **Deploy**.

La primera vez tarda entre tres y cinco minutos: instala las librerías.

### 3. Primer arranque

Al abrirse, el panel dirá que no hay datos y mostrará un botón
**"Descargar datos de mercado"**. Púlsalo. Tarda dos o tres minutos.

Después ya funciona con normalidad.

### 4. Compartir

Copia la dirección del navegador. Será algo como:

```
https://qq-quant-os.streamlit.app
```

Ese enlace lo abre cualquiera, desde cualquier dispositivo.

---

## Actualizar el panel más adelante

Cuando haya cambios en el código:

```
git add .
git commit -m "Descripcion del cambio"
git push
```

Streamlit Cloud detecta el cambio y actualiza la aplicación solo, en un minuto.

---

## Advertencias

**El repositorio es público.** Cualquiera puede leer el código. Si más adelante
la empresa quiere privacidad, hay que cambiar a un plan de pago o a otro
proveedor.

**La aplicación se duerme si nadie la usa.** Tras unos días de inactividad,
Streamlit la suspende. Se despierta sola al abrir el enlace, tardando unos
segundos más la primera vez.

**Los datos son de Yahoo Finance.** Suficientes para investigación
exploratoria, no de grado profesional. Para producción hay que contratar un
proveedor.
