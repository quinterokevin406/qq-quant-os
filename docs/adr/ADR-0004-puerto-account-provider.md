# ADR-0004 — Puerto `AccountProvider` para el estado de la cuenta

Fecha: 2026-08-18
Estado: aceptado

## Contexto

El motor de portafolio necesita equity, margen utilizado, margen disponible,
P&L flotante y el libro real de posiciones del bróker. Esa información sólo
existe dentro de MetaTrader 5.

ADR-0002 ya estableció que `qq_core` nunca importa la librería `MetaTrader5`:
es específica de Windows y ataría todo el sistema a esa plataforma. El gateway
de datos resolvió el problema por HTTP.

Queda por resolver lo mismo para el estado de la cuenta. Y hay una restricción
adicional: el gateway no existe todavía, pero el motor de portafolio se
construye ahora.

## Decisión

Se define el puerto `AccountProvider` con dos implementaciones:

**`ManualAccountProvider`** — el operador introduce balance y equity en el
perfil de cuenta. Operativo hoy. Todo el motor funciona sobre esos números.

**`MT5AccountProvider`** — estructura, firmas, unidades y pruebas de contrato
completas. El servicio HTTP al otro lado no existe; lo implementará un equipo
técnico externo.

**Regla innegociable:** un proveedor que no puede obtener un dato lanza una
excepción. Nunca devuelve cero, ni una estimación, ni el último valor conocido.

## Justificación de la regla

No es purismo. Si `margin_used` devolviera `0.0` cuando en realidad se
desconoce, el panel mostraría "margen utilizado: 0%" y el operador abriría
posiciones creyendo tener toda la capacidad libre.

Un dato ausente presentado como dato real es peor que un error visible: el
error se corrige, el dato falso se usa para decidir.

Verificado por CA-75 en CI.

## Consecuencias

**Positivas.** El motor de portafolio se construye y se prueba entero hoy. La
conexión posterior no toca ni una línea del motor. El equipo técnico recibe un
contrato ejecutable en lugar de una descripción.

**Negativas.** Mientras no exista el gateway, los límites de margen quedan
inactivos salvo que el operador introduzca el margen a mano. El panel lo
declara explícitamente en lugar de ocultarlo.

## Alternativas rechazadas

1. **Estimar el margen a partir del nocional y el apalancamiento nominal.**
   Produciría una cifra plausible y equivocada. El margen real depende del
   símbolo, del tamaño y de las reglas del bróker.

2. **Bloquear el motor de portafolio hasta que exista el gateway.** Retrasa
   meses un trabajo que puede hacerse y verificarse ya.

3. **Importar `MetaTrader5` directamente en el motor.** Contradice ADR-0002.
