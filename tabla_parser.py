import re
from typing import Optional, Dict

# Conjuntos básicos de palabras y símbolos para mapear el estado a un icono.
_CHECK_SYMBOLS = {"✅", "✔", "✓", "☑", "√"}
_CROSS_SYMBOLS = {"❌", "✖", "✕", "✗", "✘", "⚠", "⚠️"}
_CHECK_WORDS = {
    "si",
    "sí",
    "ok",
    "hecho",
    "hecha",
    "listo",
    "lista",
    "done",
    "yes",
}
_CROSS_WORDS = {
    "no",
    "pendiente",
    "pend",
    "pend.",
    "falta",
    "fail",
    "error",
    "cancelado",
    "cancelada",
}


def _normalize_hour(value: Optional[str]) -> Optional[str]:
    """Normaliza un texto de hora a formato HH:MM válido."""
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    text = text.replace(".", ":")
    match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", text)
    if not match:
        return None

    hours = int(match.group(1))
    minutes = int(match.group(2))

    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None

    return f"{hours:02}:{minutes:02}"


def _normalize_status(value: Optional[str]) -> str:
    """Convierte diferentes formas de estado en un icono estándar."""
    if not value:
        return "❔"

    text = value.strip()
    if not text:
        return "❔"

    lower = text.lower()

    if any(symbol in text for symbol in _CHECK_SYMBOLS) or lower in _CHECK_WORDS:
        return "✅"
    if any(symbol in text for symbol in _CROSS_SYMBOLS) or lower in _CROSS_WORDS:
        return "⚠️"

    return "❔"


def _parse_line(line: Optional[str]) -> Optional[Dict[str, str]]:
    """Parsea una línea sencilla en formato '<folio> <hora> <estado>'."""
    if not line:
        return None

    text = line.strip()
    if not text:
        return None

    parts = text.split()
    if len(parts) < 3:
        return None

    folio = parts[0]
    hora_raw = parts[1]
    status_raw = " ".join(parts[2:])

    hora = _normalize_hour(hora_raw)
    if hora is None:
        return None

    status_icon = _normalize_status(status_raw)

    return {"folio": folio, "hora": hora, "status": status_icon}
