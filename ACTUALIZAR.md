# Cómo actualizar sin perder tus datos

## El problema del método anterior

Hasta ahora cada versión se instalaba así: borrar `C:\dev\qq-quant-os`, extraer
un zip nuevo. Eso funciona, pero **destruye tres cosas en cada actualización**:

- `qq_data.db` — los diez años de precios descargados
- `.venv` — el entorno con todas las librerías
- `.git` — el historial, que hay que reconectar a mano cada vez

Resultado: veinte minutos de reinstalación y una descarga completa de datos.

## El método nuevo

Un comando. Conserva datos, entorno e historial.

```
cd C:\dev\qq-quant-os
```

```
git pull
```

Si el código cambió librerías o dependencias, además:

```
.venv\Scripts\Activate.ps1
```

```
pip install -e ".[dev,dashboard,data,informe]"
```

Y comprobar:

```
pytest -q
```

## Primera vez: pasar del método viejo al nuevo

Sólo hay que hacerlo una vez. A partir de ahí, `git pull` y listo.

**1.** Copia tu base de datos a un sitio seguro, fuera de la carpeta:

```
copy C:\dev\qq-quant-os\qq_data.db C:\dev\qq_data_respaldo.db
```

**2.** Borra la carpeta e instala la versión nueva del zip como siempre.

**3.** Devuelve la base de datos a su sitio:

```
copy C:\dev\qq_data_respaldo.db C:\dev\qq-quant-os\qq_data.db
```

**4.** Conecta el repositorio:

```
git init
```

```
git remote add origin https://github.com/quinterokevin406/qq-quant-os.git
```

```
git fetch origin
```

```
git reset --hard origin/main
```

Ese último comando alinea tu carpeta con GitHub sin tocar los ficheros que
`.gitignore` excluye — entre ellos `qq_data.db` y `.venv`.

## Si algo sale mal

`git pull` puede fallar si hay cambios locales sin guardar. Para descartarlos y
quedarte exactamente con lo de GitHub:

```
git reset --hard origin/main
```

Eso NO borra la base de datos ni el entorno virtual: sólo el código.
