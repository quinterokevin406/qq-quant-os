# Actualización automática a las 7:00 de Nueva York

El sistema puede descargar los datos y generar las señales solo, cada mañana,
sin que nadie tenga que ejecutar nada.

---

## Antes de empezar: qué se puede y qué no

**En el equipo de la oficina: sí.** El Programador de tareas de Windows ejecuta
el script todos los días a la hora fijada. Funciona siempre que el ordenador
esté encendido.

**En la aplicación publicada en internet: no.** El servicio gratuito de
Streamlit suspende las aplicaciones que nadie usa y no ejecuta tareas
programadas. Ahí hay que pulsar **Actualizar ahora** en el lateral.

La solución práctica es combinar ambas: el equipo de la oficina actualiza a las
7:00 y sube los datos; la aplicación publicada los recoge.

---

## Configurar la tarea en Windows

### 1. Localiza tu ruta de Python

En PowerShell, dentro de la carpeta del proyecto y con el entorno activado:

```
(Get-Command python).Source
```

Copia lo que devuelva. Será algo como:

```
C:\dev\qq-quant-os\.venv\Scripts\python.exe
```

### 2. Abre el Programador de tareas

Menú Inicio → escribe `Programador de tareas` → Enter.

### 3. Crea la tarea

En el panel derecho, **Crear tarea básica**.

**Pestaña General**
- Nombre: `QQ Quant OS - Actualizacion diaria`
- Marca **Ejecutar tanto si el usuario inició sesión como si no**
- Marca **Ejecutar con los privilegios más altos**

**Pestaña Desencadenadores** → Nuevo
- Diariamente
- Hora: **07:00**
- Repetir cada: 1 día

> Si el equipo no está en horario de Nueva York, ajusta la hora. Con España
> peninsular, las 7:00 de Nueva York son las 13:00 en horario de verano y las
> 12:00 en invierno. Con Colombia, las 7:00 de Nueva York son las 6:00 locales
> en verano y las 7:00 en invierno.

**Pestaña Acciones** → Nueva
- Acción: Iniciar un programa
- Programa o script: la ruta de Python del paso 1
- Argumentos: `scripts\actualizar_diario.py`
- Iniciar en: `C:\dev\qq-quant-os`

> El campo **Iniciar en** es el que más se olvida. Sin él, el script no
> encuentra la base de datos y falla.

**Pestaña Condiciones**
- Desmarca **Iniciar la tarea solo si el equipo está conectado a la corriente**
  si es un portátil

### 4. Comprueba que funciona

No esperes a mañana. En el Programador, clic derecho sobre la tarea →
**Ejecutar**.

Luego revisa el registro:

```
notepad logs\actualizacion.log
```

Debe mostrar la descarga, las señales generadas y el tiempo total.

---

## Qué produce cada mañana

| Archivo | Contenido |
|---|---|
| `qq_data.db` | Datos de mercado actualizados |
| `senales_del_dia.csv` | Señales operables, abrible con Excel |
| `informe_qq_quant_os.html` | Informe completo para enviar por correo |
| `logs/actualizacion.log` | Registro de lo ocurrido |

---

## Ejecutarlo a mano

```
python scripts\actualizar_diario.py
```

Sin generar el informe (más rápido):

```
python scripts\actualizar_diario.py --sin-informe
```

---

## Si algo falla

Revisa primero `logs\actualizacion.log`: ahí queda registrado todo, incluidos
los instrumentos que no respondieron.

| Síntoma en el registro | Causa probable |
|---|---|
| `La base de datos está vacía` | Falta la descarga inicial |
| Varios instrumentos fallan | Sin conexión, o el proveedor no responde |
| `El proveedor revisó N barras` | Normal y esperado: Yahoo reescribe histórico. El sistema lo detecta y conserva el valor anterior |
| La tarea no se ejecuta | Falta el campo **Iniciar en** en la configuración |
