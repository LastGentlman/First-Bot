import pytest

from chandra_ocr import ChandraOcrError
import procesar_tabla
from procesar_tabla import _extraer_detalle_error


def test_extraer_detalle_error_includes_cause_message():
    try:
        try:
            raise ValueError("detalle interno")
        except ValueError as inner_exc:
            raise RuntimeError("envoltura fallida") from inner_exc
    except RuntimeError as outer_exc:
        detalle = _extraer_detalle_error(outer_exc)

    assert "detalle interno" in detalle


def test_ejecutar_ocr_propagates_root_cause(monkeypatch):
    def fake_reader(_imagen):
        raise FileNotFoundError("ruta temporal no existe")

    monkeypatch.setattr(procesar_tabla, "leer_tabla_chandra", fake_reader)

    with pytest.raises(ChandraOcrError) as excinfo:
        procesar_tabla.ejecutar_ocr("fake-path.jpg")

    assert "ruta temporal no existe" in str(excinfo.value)
