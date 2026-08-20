"""Pruebas de la persistencia de los datos del operador.

Existen por un fallo real: las señales marcadas para seguimiento desaparecían
al día siguiente. La causa no era un error de código sino de arquitectura —se
guardaron datos irreemplazables en almacenamiento temporal— y estas pruebas
protegen la corrección.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from qq_core.domain.signal import Direction, Horizon, Signal, SignalStrength
from qq_core.execution.trade import RealTrade
from qq_core.signals.watchlist import Watchlist
from qq_core.storage.backup import (
    backup_info,
    load_backup,
    restore_if_empty,
    save_backup,
)
from qq_core.storage.user_data import (
    Persistence,
    UserDataSnapshot,
    detect_persistence,
    export_user_data,
    import_user_data,
)


@pytest.fixture(autouse=True)
def _directorio_de_copias(tmp_path, monkeypatch):
    """Aísla las copias de cada prueba."""
    monkeypatch.setenv("QQ_BACKUP_DIR", str(tmp_path / "copias"))
    monkeypatch.delenv("QQ_USER_DATA_URL", raising=False)


def _senal(symbol: str = "US500") -> Signal:
    ahora = datetime.now(timezone.utc)
    return Signal(
        signal_id=f"tf:{symbol}", strategy="trend_following",
        strategy_label="Seguimiento de tendencia", strategy_version="2.0.0",
        symbol=symbol, as_of=ahora, generated_at=ahora,
        direction=Direction.LONG, strength=SignalStrength.STRONG,
        horizon=Horizon.MONTH_1, observed_price=Decimal("5000"),
        entry_price=Decimal("5000"), target_price=Decimal("5300"),
        stop_price=Decimal("4900"), rationale="prueba",
    )


def _con_seguimiento(db: Path, n: int = 1, desde: int = 0) -> None:
    """Añade `n` señales al seguimiento, numeradas desde `desde`.

    El desplazamiento permite añadir señales NUEVAS en lugar de repetir las
    mismas: el identificador incluye símbolo y estrategia, así que volver a
    añadir SYM0 sustituye la anterior en vez de sumarse.
    """
    lista = Watchlist(db)
    for i in range(desde, desde + n):
        lista.add(_senal(f"SYM{i}"), f"nota {i}")
    lista.close()


# --------------------------------------------------------------------- #
# El fallo original
# --------------------------------------------------------------------- #


def test_el_seguimiento_sobrevive_al_borrado_de_la_base(tmp_path) -> None:
    """CA-93: LA PRUEBA QUE CUBRE EL FALLO REPORTADO.

    Streamlit Community Cloud borra el disco al reiniciarse. Las señales
    marcadas desaparecían al día siguiente. Aquí se reproduce ese borrado y
    se comprueba que la copia automática las devuelve.
    """
    db = tmp_path / "qq.db"
    _con_seguimiento(db, 3)
    assert len(export_user_data(db).watchlist) == 3

    save_backup(db)
    db.unlink()  # esto es lo que hace el servicio al reiniciarse

    restaurado = restore_if_empty(db)
    assert restaurado == {"seguimiento": 3, "operaciones": 0}
    assert len(export_user_data(db).watchlist) == 3


def test_las_notas_del_operador_se_conservan(tmp_path) -> None:
    """No basta con recuperar el símbolo: la nota es información suya."""
    db = tmp_path / "qq.db"
    lista = Watchlist(db)
    lista.add(_senal(), "entré con 2 lotes tras la ruptura")
    lista.close()

    save_backup(db)
    db.unlink()
    restore_if_empty(db)

    _, _, nota, _ = Watchlist(db).active_entries()[0]
    assert nota == "entré con 2 lotes tras la ruptura"


def test_no_se_restaura_encima_de_datos_existentes(tmp_path) -> None:
    """CA-94: restaurar sobre datos actuales sería peor que no hacer nada.

    Si el operador ya tiene señales, una copia antigua no debe sustituirlas.
    """
    db = tmp_path / "qq.db"
    _con_seguimiento(db, 1)
    save_backup(db)

    _con_seguimiento(db, 3, desde=1)  # el operador añade tres más
    assert restore_if_empty(db) is None
    assert len(export_user_data(db).watchlist) == 4


def test_una_copia_vacia_no_borra_la_buena(tmp_path) -> None:
    """CA-95: si la base se pierde, la copia anterior es lo que hay que salvar.

    Guardar una copia vacía sobre una con datos destruiría exactamente lo que
    se intenta proteger.
    """
    db = tmp_path / "qq.db"
    _con_seguimiento(db, 2)
    save_backup(db)

    db.unlink()
    assert save_backup(db) is None  # no sobrescribe

    copia = load_backup()
    assert copia is not None and len(copia.watchlist) == 2


def test_se_conservan_generaciones_anteriores(tmp_path) -> None:
    """Un borrado accidental debe poder deshacerse."""
    db = tmp_path / "qq.db"
    _con_seguimiento(db, 1)
    save_backup(db)
    _con_seguimiento(db, 2)
    save_backup(db)

    assert backup_info()["generaciones"] >= 1


# --------------------------------------------------------------------- #
# Exportación e importación manual
# --------------------------------------------------------------------- #


def test_exportar_e_importar_conserva_todo(tmp_path) -> None:
    db_origen = tmp_path / "origen.db"
    db_destino = tmp_path / "destino.db"
    _con_seguimiento(db_origen, 2)

    copia = export_user_data(db_origen)
    recuperada = UserDataSnapshot.from_json(copia.to_json())
    resultado = import_user_data(db_destino, recuperada)

    assert resultado["seguimiento"] == 2
    assert len(export_user_data(db_destino).watchlist) == 2


def test_importar_dos_veces_no_duplica(tmp_path) -> None:
    """CA-96: restaurar la misma copia tras un reinicio debe ser inocuo.

    Si duplicara, el operador no podría restaurar sin miedo y acabaría con el
    seguimiento lleno de repeticiones.
    """
    db_origen = tmp_path / "origen.db"
    db_destino = tmp_path / "destino.db"
    _con_seguimiento(db_origen, 3)

    copia = export_user_data(db_origen)
    import_user_data(db_destino, copia)
    import_user_data(db_destino, copia)

    assert len(export_user_data(db_destino).watchlist) == 3


def test_sustituir_reemplaza_en_lugar_de_fusionar(tmp_path) -> None:
    db_origen = tmp_path / "origen.db"
    db_destino = tmp_path / "destino.db"
    _con_seguimiento(db_origen, 2)
    _con_seguimiento(db_destino, 5)

    import_user_data(db_destino, export_user_data(db_origen), replace=True)
    assert len(export_user_data(db_destino).watchlist) == 2


def test_fichero_ajeno_se_rechaza() -> None:
    """Un archivo cualquiera no debe interpretarse como copia válida."""
    with pytest.raises(ValueError, match="QQ Quant OS"):
        UserDataSnapshot.from_json(json.dumps({"cosa": "otra"}))


def test_fichero_corrupto_se_rechaza() -> None:
    with pytest.raises(ValueError, match="JSON"):
        UserDataSnapshot.from_json("{esto no es json")


def test_la_exportacion_no_incluye_precios(tmp_path) -> None:
    """CA-97: los precios se regeneran; incluirlos haría el fichero enorme.

    La copia contiene sólo lo irreemplazable.
    """
    db = tmp_path / "qq.db"
    _con_seguimiento(db, 1)

    contenido = json.loads(export_user_data(db).to_json())
    assert set(contenido) == {"formato", "exportado", "seguimiento", "operaciones"}


def test_operaciones_reales_tambien_se_copian(tmp_path) -> None:
    """El diario de operaciones es tan irreemplazable como el seguimiento."""
    from qq_core.execution.journal import TradeJournal

    db = tmp_path / "qq.db"
    diario = TradeJournal(db)
    diario.record(
        RealTrade(
            trade_id="US500:2026-08-01", symbol="US500",
            direction=Direction.LONG, units=Decimal("2"),
            entry_date=date(2026, 8, 1), entry_price=Decimal("5000"),
        )
    )
    diario.close()

    save_backup(db)
    db.unlink()
    restaurado = restore_if_empty(db)

    assert restaurado["operaciones"] == 1


# --------------------------------------------------------------------- #
# Detección del entorno
# --------------------------------------------------------------------- #


def test_detecta_almacenamiento_temporal_en_la_nube(tmp_path, monkeypatch) -> None:
    """CA-98: el operador debe saber si sus datos corren peligro.

    Callarlo sería peor que el propio fallo: creería que están a salvo.
    """
    monkeypatch.setenv("STREAMLIT_SERVER_HEADLESS", "1")
    nivel = detect_persistence(tmp_path / "qq.db")
    assert nivel is Persistence.EPHEMERAL
    assert not nivel.is_durable
    assert nivel.warning and "no se conservan" in nivel.warning


def test_detecta_base_externa(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QQ_USER_DATA_URL", "postgresql://ejemplo")
    nivel = detect_persistence(tmp_path / "qq.db")
    assert nivel is Persistence.EXTERNAL
    assert nivel.is_durable
    assert nivel.warning is None


def test_detecta_almacenamiento_local(tmp_path) -> None:
    nivel = detect_persistence(tmp_path / "qq.db")
    assert nivel is Persistence.LOCAL
    assert nivel.is_durable


# --------------------------------------------------------------------- #
# Base de datos remota
# --------------------------------------------------------------------- #


def test_sin_url_el_sistema_funciona_igual(monkeypatch) -> None:
    """CA-99: la base remota es opcional, no un requisito.

    Quien use el sistema en su ordenador no debe necesitar configurar nada.
    """
    from qq_core.storage.remote import is_configured, remote_status

    monkeypatch.delenv("QQ_USER_DATA_URL", raising=False)
    assert not is_configured()

    estado = remote_status()
    assert estado["configurada"] is False
    assert estado["conectada"] is False


def test_la_contrasena_nunca_se_muestra(monkeypatch) -> None:
    """CA-100: la cadena de conexión lleva credenciales.

    Mostrarla entera en el panel la expondría en cualquier captura de
    pantalla, que es exactamente como se filtran las claves.
    """
    from qq_core.storage.remote import masked_url

    monkeypatch.setenv(
        "QQ_USER_DATA_URL",
        "postgresql://usuario:CLAVE_SECRETA@servidor.supabase.co:5432/postgres",
    )
    enmascarada = masked_url()
    assert "CLAVE_SECRETA" not in enmascarada
    assert "usuario" in enmascarada
    assert "servidor.supabase.co" in enmascarada


def test_fallo_de_conexion_se_informa_sin_romper(monkeypatch) -> None:
    """CA-101: si el servicio remoto cae, el sistema sigue funcionando.

    Una caída de Supabase no debe dejar al operador sin poder consultar sus
    señales: se avisa y se usa el almacén local.
    """
    from qq_core.storage.remote import remote_status

    monkeypatch.setenv(
        "QQ_USER_DATA_URL",
        "postgresql://x:y@servidor-que-no-existe.invalid:5432/postgres",
    )
    estado = remote_status()  # no debe lanzar excepción
    assert estado["configurada"] is True
    assert estado["conectada"] is False
    assert "error" in estado


def test_sincronizacion_sin_remoto_devuelve_none(tmp_path, monkeypatch) -> None:
    from qq_core.storage.remote import pull_from_remote, push_to_remote

    monkeypatch.delenv("QQ_USER_DATA_URL", raising=False)
    db = tmp_path / "qq.db"
    _con_seguimiento(db, 1)

    assert push_to_remote(db) is None
    assert pull_from_remote(db) is None


def test_solo_se_permiten_las_tablas_del_operador(monkeypatch) -> None:
    """CA-102: los nombres de tabla no se interpolan sin validar.

    Construir SQL concatenando texto es la vía habitual de inyección. Aquí el
    nombre viene del código, no del usuario, pero la comprobación evita que un
    cambio futuro abra el agujero.
    """
    import inspect

    from qq_core.storage import remote

    fuente = inspect.getsource(remote.PostgresUserStore.replace_all)
    assert "Tabla no permitida" in fuente
    fuente_lectura = inspect.getsource(remote.PostgresUserStore.read_all)
    assert "Tabla no permitida" in fuente_lectura
