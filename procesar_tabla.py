import easyocr
import re
import logging
from functools import lru_cache
from typing import Optional, List, Dict, Any, Tuple

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

DEFAULT_PREFIJO = "168"
CHECK_SYMBOLS = {"✅", "✔", "✓", "☑", "√"}
CROSS_SYMBOLS = {"❌", "✖", "✕", "✗", "✘", "⚠", "⚠️"}
CHECK_WORDS = {"ok", "si", "sí", "hecho", "listo", "done", "v", "va", "yes"}
CROSS_WORDS = {"no", "pendiente", "falta", "x", "fail"}

reader = easyocr.Reader(["es", "en"], gpu=False)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_URL y SUPABASE_KEY no están configuradas. "
            "Define las variables de entorno antes de ejecutar la aplicación."
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def limpiar_hora(hora_raw: str) -> Optional[str]:
    """Normaliza texto OCR de hora (corrige caracteres comunes y valida formato HH:MM)."""
    if not hora_raw:
        return None

    hora = hora_raw.strip()
    if not hora:
        return None

    reemplazos = {
        "l": "1",
        "I": "1",
        "|": "1",
        "O": "0",
        "o": "0",
        "S": "5",
        "s": "5",
        "B": "8",
        "v": "4",
        "V": "4",
        "-": ":",
        "—": ":",
        "–": ":",
        ";": ":",
        ",": ":",
        ".": ":",
        "*": ":",
        "·": ":",
        " ": "",
    }

    for caracter, reemplazo in reemplazos.items():
        hora = hora.replace(caracter, reemplazo)

    hora = re.sub(r"[^0-9:]", "", hora)

    if hora.count(":") > 1:
        primera_pos = hora.find(":")
        hora = hora[: primera_pos + 1] + hora[primera_pos + 1 :].replace(":", "")

    if ":" not in hora and len(hora) == 4:
        hora = f"{hora[:2]}:{hora[2:]}"

    match = re.match(r"^(\d{1,2}):(\d{2})$", hora)
    if not match:
        return None

    h, m = int(match[1]), match[2]
    if h > 29 or int(m) > 59:
        return None

    return f"{h:02}:{m}"


def valor_orden_hora(hora: str) -> int:
    """Convierte una hora en minutos corridos con offset para madrugada."""
    if not hora or ":" not in hora:
        raise ValueError(f"Hora inválida para ordenar: {hora!r}")

    try:
        h_str, m_str = hora.split(":")
        h = int(h_str)
        m = int(m_str)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Hora inválida para ordenar: {hora!r}") from exc

    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Hora fuera de rango para ordenar: {hora!r}")

    if h < 5:
        h += 24

    return h * 60 + m


def limpiar_estado(simbolo: str) -> str:
    """Convierte símbolos OCR a estado de texto."""
    if not simbolo:
        return "indefinido"

    simbolo_limpio = simbolo.strip()
    if not simbolo_limpio:
        return "indefinido"

    simbolo_lower = simbolo_limpio.lower()

    if any(mark in simbolo_limpio for mark in CHECK_SYMBOLS) or simbolo_lower in CHECK_WORDS:
        return "completado"
    if any(mark in simbolo_limpio for mark in CROSS_SYMBOLS) or simbolo_lower in CROSS_WORDS:
        return "pendiente"
    if "?" in simbolo_limpio:
        return "pendiente"

    return "indefinido"


def estado_a_icono(estado: str) -> str:
    """Mapea el estado textual a un icono."""
    if not estado:
        return "❔"
    estado_normalizado = estado.strip().lower()
    if estado_normalizado == "completado":
        return "✅"
    if estado_normalizado == "pendiente":
        return "⚠️"
    return "❔"


def es_comilla(texto: str) -> bool:
    """Detecta si un texto es una comilla o símbolo de repetición."""
    texto_limpio = texto.strip()
    return texto_limpio in ['"', "'", "''", '""', ",,", "٠٠", "ʻʻ", "``"]


def normalizar_token(texto: str) -> str:
    """Limpia ruido común del OCR (comillas, espacios extra)."""
    if not texto:
        return ""

    texto_limpio = texto.strip()
    if not texto_limpio:
        return ""

    reemplazos_comillas = {
        "“": '"',
        "”": '"',
        "«": '"',
        "»": '"',
    }

    for original, reemplazo in reemplazos_comillas.items():
        texto_limpio = texto_limpio.replace(original, reemplazo)

    marcas_repeticion = {'"', "'", "''", '""', ",,", "٠٠", "ʻʻ", "``"}
    if texto_limpio in marcas_repeticion:
        return texto_limpio

    # Conserva la comilla doble si es el único carácter del token (marca de repetición habitual).
    if len(texto_limpio) > 1:
        texto_limpio = texto_limpio.replace('"', "")

    return texto_limpio


def detectar_estado_token(token: str) -> Optional[str]:
    """Intenta inferir el estado a partir de un token OCR."""
    if not token:
        return None

    token_limpio = token.strip()
    if not token_limpio:
        return None

    token_lower = token_limpio.lower()

    if token_lower in CHECK_WORDS or any(mark in token_limpio for mark in CHECK_SYMBOLS):
        return "completado"
    if token_lower in CROSS_WORDS or any(mark in token_limpio for mark in CROSS_SYMBOLS):
        return "pendiente"

    return None


def _misma_fila(fila: Dict[str, float], elemento: Dict[str, float], tolerancia: float = 0.45) -> bool:
    """Determina si un token pertenece a la misma fila en función de su posición vertical."""
    fila_altura = max(fila["y_max"] - fila["y_min"], 1.0)
    elemento_altura = max(elemento["height"], 1.0)
    margen = max(fila_altura, elemento_altura) * tolerancia

    por_debajo = elemento["y_min"] > fila["y_max"] + margen
    por_encima = elemento["y_max"] < fila["y_min"] - margen

    return not (por_debajo or por_encima)


def ordenar_tokens_por_posicion(resultados_ocr: List[Any]) -> Tuple[List[str], List[List[str]]]:
    """
    Utiliza las coordenadas devueltas por EasyOCR para ordenar los tokens de izquierda a derecha y de arriba hacia abajo.
    Devuelve la lista plana de tokens en orden de lectura y, adicionalmente, las filas detectadas para depuración.
    """
    elementos: List[Dict[str, Any]] = []

    for entrada in resultados_ocr:
        if not entrada:
            continue

        if len(entrada) == 3:
            bbox, texto, confianza = entrada
        elif len(entrada) == 2:
            bbox, texto = entrada
            confianza = None
        else:
            logger.debug(f"Formato de resultado OCR inesperado: {entrada}")
            continue

        texto_crudo = (texto or "").strip()
        if not texto_crudo:
            continue

        try:
            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]
        except (TypeError, ValueError) as exc:
            logger.debug(f"No se pudieron extraer coordenadas de {entrada}: {exc}")
            continue

        x_min = min(xs)
        x_max = max(xs)
        y_min = min(ys)
        y_max = max(ys)

        elementos.append(
            {
                "texto": texto_crudo,
                "confianza": confianza,
                "x_min": x_min,
                "x_max": x_max,
                "y_min": y_min,
                "y_max": y_max,
                "center_x": (x_min + x_max) / 2,
                "center_y": (y_min + y_max) / 2,
                "width": max(x_max - x_min, 1.0),
                "height": max(y_max - y_min, 1.0),
            }
        )

    if not elementos:
        return [], []

    filas: List[Dict[str, Any]] = []

    for elemento in sorted(elementos, key=lambda e: e["center_y"]):
        asignado = False
        for fila in filas:
            if _misma_fila(fila, elemento):
                fila["elementos"].append(elemento)
                fila["y_min"] = min(fila["y_min"], elemento["y_min"])
                fila["y_max"] = max(fila["y_max"], elemento["y_max"])
                asignado = True
                break
        if not asignado:
            filas.append(
                {
                    "elementos": [elemento],
                    "y_min": elemento["y_min"],
                    "y_max": elemento["y_max"],
                }
            )

    filas_ordenadas = sorted(filas, key=lambda f: f["y_min"])
    filas_texto: List[List[str]] = []
    tokens_ordenados: List[str] = []

    for fila in filas_ordenadas:
        fila["elementos"].sort(key=lambda e: e["center_x"])
        fila_texto = [elem["texto"] for elem in fila["elementos"]]
        filas_texto.append(fila_texto)
        tokens_ordenados.extend(fila_texto)

    return tokens_ordenados, filas_texto


def extraer_filas_lineal(textos: List[str], prefijo_manual: Optional[str] = None) -> List[Dict[str, Optional[str]]]:
    """
    Extrae filas recorriendo los tokens en orden y aplicando reglas heurísticas.
    Devuelve una lista de diccionarios con claves: letra, prefijo, folio, folio_base, hora, estado, icono.
    """
    filas: List[Dict[str, Optional[str]]] = []
    prefijo_actual = prefijo_manual
    letra_actual: Optional[str] = None
    ultima_letra_reportada: Optional[str] = None
    ultimo_folio_completo: Optional[str] = None
    ultimo_folio_base: Optional[str] = None
    fila_actual: Dict[str, Optional[str]] = {}

    def prefijo_por_defecto() -> str:
        return prefijo_actual or prefijo_manual or DEFAULT_PREFIJO

    def commit():
        nonlocal fila_actual, prefijo_actual, ultima_letra_reportada, ultimo_folio_completo, ultimo_folio_base
        if not fila_actual:
            return

        folio_completo = fila_actual.get("folio")
        hora_val = fila_actual.get("hora")

        if not folio_completo or not hora_val:
            fila_actual = {}
            return

        prefijo_val = fila_actual.get("prefijo") or prefijo_por_defecto()
        folio_base = fila_actual.get("folio_base")

        prefijo_val_str = str(prefijo_val) if prefijo_val is not None else ""
        if not folio_base and prefijo_val_str and folio_completo.startswith(prefijo_val_str):
            folio_base = folio_completo[len(prefijo_val_str) :]

        estado_texto = fila_actual.get("estado") or "indefinido"
        icono = fila_actual.get("icono") or estado_a_icono(estado_texto)
        letra = fila_actual.get("letra") or letra_actual or ultima_letra_reportada

        fila = {
            "letra": letra,
            "prefijo": prefijo_val,
            "folio": folio_completo,
            "folio_base": folio_base,
            "hora": hora_val,
            "estado": estado_texto,
            "icono": icono,
        }

        filas.append(fila)

        if letra:
            ultima_letra_reportada = letra
        if folio_completo:
            ultimo_folio_completo = folio_completo
        if folio_base:
            ultimo_folio_base = folio_base
        if prefijo_val:
            prefijo_actual = prefijo_val

        fila_actual = {}

    for idx, token in enumerate(textos):
        token_normalizado = normalizar_token(token)
        if not token_normalizado:
            continue

        logger.debug(f"Token {idx}: '{token}' -> '{token_normalizado}'")

        if es_comilla(token_normalizado):
            if not fila_actual:
                fila_actual = {
                    "letra": letra_actual or ultima_letra_reportada,
                    "prefijo": prefijo_por_defecto(),
                    "folio": ultimo_folio_completo,
                    "folio_base": ultimo_folio_base,
                }
            else:
                fila_actual.setdefault("letra", letra_actual or ultima_letra_reportada)
                if ultimo_folio_completo and not fila_actual.get("folio"):
                    fila_actual["folio"] = ultimo_folio_completo
                    fila_actual["folio_base"] = ultimo_folio_base
                    fila_actual.setdefault("prefijo", prefijo_por_defecto())
            continue

        if len(token_normalizado) == 1 and token_normalizado.isalpha():
            commit()
            letra_actual = token_normalizado.upper()
            continue

        if re.fullmatch(r"\d{3,7}", token_normalizado):
            commit()
            numero = token_normalizado
            if len(numero) > 5:
                prefijo_actual = numero[:3]
                folio_base = numero[len(prefijo_actual) :] or numero[3:]
                if not folio_base:
                    folio_base = numero[len(prefijo_actual) :]
                folio_completo = numero
                prefijo_fila = prefijo_actual
            else:
                prefijo_fila = prefijo_por_defecto()
                prefijo_actual = prefijo_fila
                folio_base = numero
                folio_completo = f"{prefijo_fila}{folio_base}" if prefijo_fila else numero

            fila_actual = {
                "letra": letra_actual or ultima_letra_reportada,
                "prefijo": prefijo_fila,
                "folio": folio_completo,
                "folio_base": folio_base,
            }
            continue

        if re.fullmatch(r"\d{1,2}[:.]\d{2}", token_normalizado):
            hora_val = limpiar_hora(token_normalizado.replace(".", ":"))
            if not hora_val:
                continue

            if not fila_actual:
                fila_actual = {
                    "letra": letra_actual or ultima_letra_reportada,
                    "prefijo": prefijo_por_defecto(),
                    "folio": ultimo_folio_completo,
                    "folio_base": ultimo_folio_base,
                }

            fila_actual["hora"] = hora_val
            continue

        estado_detectado = detectar_estado_token(token_normalizado)
        if estado_detectado:
            if not fila_actual:
                fila_actual = {
                    "letra": letra_actual or ultima_letra_reportada,
                    "prefijo": prefijo_por_defecto(),
                    "folio": ultimo_folio_completo,
                    "folio_base": ultimo_folio_base,
                }

            fila_actual["estado"] = estado_detectado
            fila_actual["icono"] = estado_a_icono(estado_detectado)

            if fila_actual.get("folio") and fila_actual.get("hora"):
                commit()
            continue

        if fila_actual.get("folio") and fila_actual.get("hora"):
            commit()

    commit()

    return filas


def procesar_tabla(imagen: str, prefijo_manual: Optional[str] = None):
    """Lee una tabla escrita a mano con EasyOCR, limpia, ordena y guarda en Supabase."""
    try:
        logger.info(f"Iniciando procesamiento de tabla desde imagen: {imagen}")

        try:
            result = reader.readtext(imagen, detail=1, paragraph=False)
        except Exception as e:
            logger.error(f"Error en OCR: {e}")
            return f"Error: No se pudo leer el texto de la imagen. {str(e)}"

        if not result:
            return "Error: No se detectó texto en la imagen."

        logger.info(f"OCR detectó {len(result)} elementos brutos")
        logger.debug(f"Elementos OCR brutos: {result}")

        tokens_ordenados, filas_detectadas = ordenar_tokens_por_posicion(result)

        if not tokens_ordenados:
            return "Error: No se pudo reconstruir el orden de lectura de la tabla."

        logger.info(f"{len(tokens_ordenados)} tokens tras ordenar por posición (filas detectadas: {len(filas_detectadas)})")
        logger.debug(f"Filas detectadas (OCR): {filas_detectadas}")

        textos: List[str] = []
        for raw in tokens_ordenados:
            normalizado = normalizar_token(raw)
            if normalizado:
                textos.append(normalizado)

        logger.info(f"{len(textos)} tokens normalizados tras limpieza")
        logger.debug(f"Tokens normalizados ordenados: {textos}")

        filas = extraer_filas_lineal(textos, prefijo_manual)

        if not filas:
            return f"Error: No se pudieron extraer filas válidas. Elementos detectados: {textos[:10]}"

        logger.info(f"Se extrajeron {len(filas)} filas candidatas")

        filas_en_orden = filas

        registros_preparados: List[Dict[str, str]] = []
        for idx, fila in enumerate(filas_en_orden, start=1):
            letra = fila.get("letra") or f"L{idx}"
            folio_completo = fila.get("folio")
            hora_val = fila.get("hora")
            estado_texto = fila.get("estado") or "indefinido"
            icono = fila.get("icono") or estado_a_icono(estado_texto)

            if not folio_completo or not hora_val:
                logger.debug(f"Fila descartada (datos incompletos): {fila}")
                continue

            registros_preparados.append(
                {
                    "id": str(letra).strip(),
                    "folio": str(folio_completo).strip(),
                    "hora": str(hora_val).strip(),
                    "estado": str(estado_texto).strip(),
                    "icono": icono,
                }
            )

        if not registros_preparados:
            return "Error: No se pudieron preparar registros válidos a partir del OCR."

        try:
            supabase = get_supabase_client()
            registros_insertados = 0
            errores = []

            for row in registros_preparados:
                try:
                    datos_insert = {
                        "id": row["id"],
                        "folio": row["folio"],
                        "hora": row["hora"],
                        "estado": row["estado"],
                    }

                    if not all(datos_insert.values()):
                        errores.append(f"Datos vacíos: {datos_insert}")
                        continue

                    supabase.table("registros").insert(datos_insert).execute()
                    registros_insertados += 1
                    logger.debug(f"Insertado: {datos_insert}")

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Error insertando {row}: {error_msg}")

                    if "duplicate key" in error_msg.lower():
                        errores.append(f"ID duplicado: {row.get('id')}")
                    else:
                        errores.append(f"Error DB: {error_msg[:50]}")

            mensaje = f"Procesados {len(registros_preparados)} registros, {registros_insertados} insertados exitosamente."

            tabla_lineas = ["Folio | Hora | Status", "----- | ---- | ------"]
            for row in registros_preparados:
                tabla_lineas.append(f"{row['id']}{row['folio']} | {row['hora']} | {row['icono']}")
            mensaje += "\n\n" + "\n".join(tabla_lineas)

            if errores:
                mensaje += f"\n\n⚠️ {len(errores)} error(es): {'; '.join(errores[:2])}"

            return mensaje

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error Supabase: {error_msg}", exc_info=True)
            return f"Error: No se pudo conectar a Supabase. {error_msg[:100]}"

    except Exception as e:
        logger.error(f"Error general: {e}", exc_info=True)
        return f"Error: {str(e)}"