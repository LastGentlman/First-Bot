import easyocr
import re
import logging
from functools import lru_cache
from typing import Optional, List, Dict
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

reader = easyocr.Reader(["es"], gpu=False)


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
    """Convierte símbolos OCR a estado de texto"""
    s = simbolo.strip().lower()
    if any(x in s for x in ["✔", "✓", "√", "v"]):
        return "completado"
    if any(x in s for x in ["⚠", "x", "?"]):
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
    """Detecta si un texto es una comilla o símbolo de repetición"""
    texto_limpio = texto.strip()
    return texto_limpio in ['"', "'", "''", '""', ",,", "٠٠", "ʻʻ", "``"]


def extraer_filas_secuenciales(result: List[str]) -> List[Dict]:
    """
    Extrae filas de la tabla procesando elementos secuencialmente.
    Detecta patrón: [Letra/Comilla] [Folio/Comilla] [Hora] [Estado opcional]
    """
    filas = []
    ultima_letra = None
    ultimo_folio_base = None

    i = 0
    while i < len(result):
        texto = result[i].strip()
        logger.debug(f"Posición {i}: '{texto}'")

        if not texto:
            i += 1
            continue

        fila: Dict[str, Optional[str]] = {}

        # 1. Detectar LETRA o COMILLA
        if es_comilla(texto):
            fila["letra"] = ultima_letra
            logger.debug(f"  -> Letra (comilla): {ultima_letra}")
            i += 1
        elif len(texto) == 1 and texto.isalpha():
            fila["letra"] = texto.upper()
            ultima_letra = fila["letra"]
            logger.debug(f"  -> Letra: {fila['letra']}")
            i += 1
        else:
            fila["letra"] = ultima_letra
            logger.debug(f"  -> Letra (default): {ultima_letra}")

        # 2. Detectar FOLIO o COMILLA
        if i < len(result):
            texto = result[i].strip()
            logger.debug(f"Posición {i}: '{texto}'")

            if es_comilla(texto):
                fila["folio"] = ultimo_folio_base
                logger.debug(f"  -> Folio (comilla): {ultimo_folio_base}")
                i += 1
            else:
                folio_match = re.search(r"(\d{4})", texto)
                if folio_match:
                    fila["folio"] = folio_match.group(1)
                    ultimo_folio_base = fila["folio"]
                    logger.debug(f"  -> Folio: {fila['folio']}")
                    i += 1
                else:
                    fila["folio"] = ultimo_folio_base
                    logger.debug(f"  -> Folio (default): {ultimo_folio_base}")

        # 3. Detectar HORA
        if i < len(result):
            texto = result[i].strip()
            logger.debug(f"Posición {i}: '{texto}'")

            hora_limpia = limpiar_hora(texto)
            if hora_limpia:
                fila["hora"] = hora_limpia
                logger.debug(f"  -> Hora: {fila['hora']}")
                i += 1
            else:
                logger.debug("  -> No se detectó hora válida")
                i += 1
                continue
        else:
            break

        # 4. Detectar ESTADO (opcional)
        if i < len(result):
            texto = result[i].strip()
            logger.debug(f"Posición {i}: '{texto}'")

            estado = limpiar_estado(texto)
            if estado != "indefinido":
                fila["estado"] = estado
                logger.debug(f"  -> Estado: {fila['estado']}")
                i += 1
            else:
                if i + 1 < len(result):
                    siguiente = result[i + 1].strip()
                    estado_sig = limpiar_estado(siguiente)
                    if estado_sig != "indefinido":
                        fila["estado"] = estado_sig
                        logger.debug(f"  -> Estado (siguiente): {fila['estado']}")
                        i += 2
                    else:
                        fila["estado"] = "indefinido"
                        logger.debug("  -> Estado: indefinido")
                else:
                    fila["estado"] = "indefinido"
                    logger.debug("  -> Estado: indefinido")
        else:
            fila["estado"] = "indefinido"

        if fila.get("folio") and fila.get("hora"):
            filas.append(fila)
            logger.info(f"Fila añadida: {fila}")
        else:
            logger.debug(f"Fila descartada (datos incompletos): {fila}")

    return filas


def procesar_tabla(imagen):
    """Lee una tabla escrita a mano con EasyOCR, limpia, ordena y guarda en Supabase"""
    try:
        logger.info(f"Iniciando procesamiento de tabla desde imagen: {imagen}")

        try:
            result = reader.readtext(imagen, detail=0)
        except Exception as e:
            logger.error(f"Error en OCR: {e}")
            return f"Error: No se pudo leer el texto de la imagen. {str(e)}"

        if not result or len(result) == 0:
            return "Error: No se detectó texto en la imagen."

        logger.info(f"OCR detectó {len(result)} elementos")
        logger.info(f"Elementos OCR: {result}")

        prefijo_detectado = "168"
        for text in result:
            text_clean = text.strip()
            if re.match(r"^\d{7}$", text_clean):
                prefijo_detectado = text_clean[:3]
                logger.info(f"Prefijo detectado: {prefijo_detectado}")
                break

        filas = extraer_filas_secuenciales(result)

        if not filas:
            return f"Error: No se pudieron extraer filas válidas. Elementos detectados: {result[:10]}"

        logger.info(f"Se extrajeron {len(filas)} filas")

        datos_finales = []
        for idx, fila in enumerate(filas):
            letra = fila.get("letra") or f"L{idx + 1}"
            folio_base = fila["folio"]
            folio_completo = f"{prefijo_detectado}{folio_base}"
            hora = fila["hora"]
            estado = fila.get("estado", "indefinido")

            datos_finales.append(
                {
                    "id": letra,
                    "folio": folio_base,
                    "folio_completo": folio_completo,
                    "hora": hora,
                    "estado": estado,
                }
            )

        try:
            datos_ordenados = sorted(datos_finales, key=lambda x: valor_orden_hora(x["hora"]))
        except ValueError as e:
            logger.warning(f"Error ordenando: {e}")
            datos_ordenados = datos_finales

        tabla_filas = []
        for row in datos_ordenados:
            folio_con_letra = f"{row['id']}{row['folio_completo']}"
            tabla_filas.append(
                {
                    "folio": folio_con_letra,
                    "hora": row["hora"],
                    "icono": estado_a_icono(row["estado"]),
                }
            )

        try:
            supabase = get_supabase_client()
            registros_insertados = 0
            errores = []

            for row in datos_ordenados:
                try:
                    datos_insert = {
                        "id": str(row["id"]).strip(),
                        "folio": str(row["folio_completo"]).strip(),
                        "hora": str(row["hora"]).strip(),
                        "estado": str(row["estado"]).strip(),
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

            logger.info(f"Insertados {registros_insertados} de {len(datos_ordenados)}")

            mensaje = f"Procesados {len(datos_ordenados)} registros, {registros_insertados} insertados exitosamente."

            if tabla_filas:
                tabla_lineas = ["Folio | Hora | Status", "----- | ---- | ------"]
                for fila in tabla_filas:
                    tabla_lineas.append(f"{fila['folio']} | {fila['hora']} | {fila['icono']}")
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