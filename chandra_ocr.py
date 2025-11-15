import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import httpx

logger = logging.getLogger(__name__)

ReadableImage = Union[str, bytes, bytearray]


class ChandraOcrError(RuntimeError):
    """Error base para errores provenientes de Chandra OCR."""


class ChandraOcrConfigurationError(ChandraOcrError):
    """Se lanza cuando falta configuración obligatoria."""


class ChandraOcrResponseError(ChandraOcrError):
    """Se lanza cuando la API responde con datos inválidos."""


def _get_config() -> Dict[str, Any]:
    timeout_env = os.getenv("CHANDRA_TIMEOUT", "").strip()
    try:
        timeout = float(timeout_env) if timeout_env else 30.0
    except ValueError:
        logger.warning("Valor inválido para CHANDRA_TIMEOUT=%s. Usando 30s.", timeout_env)
        timeout = 30.0

    config = {
        "api_url": os.getenv("CHANDRA_API_URL", "https://api.chandra-ocr.com/v1/table"),
        "api_key": os.getenv("CHANDRA_API_KEY", "").strip(),
        "model": os.getenv("CHANDRA_MODEL", "chandra-table-latest"),
        "timeout": timeout,
    }

    if not config["api_key"]:
        raise ChandraOcrConfigurationError("CHANDRA_API_KEY no está definido.")

    return config


def _load_image_bytes(imagen: ReadableImage) -> bytes:
    if isinstance(imagen, (bytes, bytearray)):
        return bytes(imagen)

    if isinstance(imagen, str):
        if not os.path.exists(imagen):
            raise FileNotFoundError(f"No se encontró la imagen en la ruta proporcionada: {imagen}")
        with open(imagen, "rb") as file:
            return file.read()

    if hasattr(imagen, "read"):
        contenido = imagen.read()
        if isinstance(contenido, str):
            return contenido.encode("latin-1")
        return bytes(contenido)

    raise TypeError("El parámetro 'imagen' debe ser bytes, bytearray, ruta de archivo o file-like object.")


def _build_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
    }


def _post_ocr_request(
    *,
    client: httpx.Client,
    api_url: str,
    headers: Dict[str, str],
    model: str,
    image_bytes: bytes,
) -> Dict[str, Any]:
    files = {
        "file": ("table.jpg", image_bytes, "application/octet-stream"),
    }
    data = {"model": model, "output_format": "table"}

    response = client.post(api_url, headers=headers, files=files, data=data)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detalle = exc.response.text[:500]
        raise ChandraOcrResponseError(f"Chandra OCR devolvió {exc.response.status_code}: {detalle}") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise ChandraOcrResponseError("Chandra OCR devolvió una respuesta no JSON.") from exc


def _dig(data: Dict[str, Any], *keys: str) -> Optional[Any]:
    cursor: Any = data
    for key in keys:
        if isinstance(cursor, dict):
            cursor = cursor.get(key)
        else:
            return None
    return cursor


def _normalize_polygon(
    raw_bbox: Optional[Any],
    *,
    row_idx: Optional[int] = None,
    col_idx: Optional[int] = None,
) -> Optional[List[List[float]]]:
    if not raw_bbox:
        return _synthetic_bbox(row_idx, col_idx)

    if isinstance(raw_bbox, dict):
        if {"x", "y", "width", "height"} <= raw_bbox.keys():
            x = float(raw_bbox["x"])
            y = float(raw_bbox["y"])
            width = float(raw_bbox["width"])
            height = float(raw_bbox["height"])
            return [
                [x, y],
                [x + width, y],
                [x + width, y + height],
                [x, y + height],
            ]
        if "vertices" in raw_bbox:
            vertices = raw_bbox["vertices"]
            if isinstance(vertices, list):
                puntos = []
                for vertex in vertices:
                    if isinstance(vertex, dict) and "x" in vertex and "y" in vertex:
                        puntos.append([float(vertex["x"]), float(vertex["y"])])
                if len(puntos) >= 4:
                    return puntos[:4]

    if isinstance(raw_bbox, list):
        if raw_bbox and isinstance(raw_bbox[0], (list, tuple)):
            try:
                return [[float(p[0]), float(p[1])] for p in raw_bbox[:4]]
            except (ValueError, TypeError):
                return None
        if len(raw_bbox) in {4, 8}:
            try:
                if len(raw_bbox) == 4:
                    x, y, w, h = raw_bbox
                    return [
                        [float(x), float(y)],
                        [float(x) + float(w), float(y)],
                        [float(x) + float(w), float(y) + float(h)],
                        [float(x), float(y) + float(h)],
                    ]
                puntos = []
                for idx in range(0, len(raw_bbox), 2):
                    puntos.append([float(raw_bbox[idx]), float(raw_bbox[idx + 1])])
                if len(puntos) >= 4:
                    return puntos[:4]
            except (ValueError, TypeError):
                return None

    return _synthetic_bbox(row_idx, col_idx)


def _synthetic_bbox(row_idx: Optional[int], col_idx: Optional[int]) -> Optional[List[List[float]]]:
    if row_idx is None and col_idx is None:
        return None

    base_y = float((row_idx or 0) * 120)
    top = base_y + 5.0
    bottom = base_y + 65.0

    base_x = float((col_idx or 0) * 240)
    left = base_x + 5.0
    right = base_x + 200.0

    return [
        [left, top],
        [right, top],
        [right, bottom],
        [left, bottom],
    ]


def _bbox_center(bbox: Sequence[Sequence[float]]) -> Optional[Tuple[float, float]]:
    if not bbox:
        return None
    try:
        xs = [pt[0] for pt in bbox]
        ys = [pt[1] for pt in bbox]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    except (TypeError, ZeroDivisionError):
        return None


def _collect_cells_from_tables(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    tablas = _dig(payload, "data", "tables") or payload.get("tables")
    if not isinstance(tablas, list):
        return []

    celdas: List[Dict[str, Any]] = []
    for tabla_idx, tabla in enumerate(tablas):
        filas = tabla.get("rows") or []
        if not isinstance(filas, list):
            continue
        for fila_idx, fila in enumerate(filas):
            fila_bbox = fila.get("bbox") or fila.get("bounding_box") or fila.get("polygon")
            celdas_fila = fila.get("cells") or fila.get("values") or []
            for col_idx, celda in enumerate(celdas_fila):
                texto = (
                    celda.get("text")
                    or celda.get("value")
                    or celda.get("content")
                    or celda.get("raw_text")
                )
                if not texto:
                    continue

                bbox = (
                    celda.get("bbox")
                    or celda.get("bounding_box")
                    or celda.get("polygon")
                    or fila_bbox
                )
                poligono = _normalize_polygon(bbox, row_idx=fila_idx, col_idx=col_idx)
                if not poligono:
                    continue

                confianza = (
                    celda.get("confidence")
                    or celda.get("score")
                    or fila.get("confidence")
                    or tabla.get("confidence")
                    or 0.9
                )

                celdas.append(
                    {
                        "text": texto,
                        "confidence": float(confianza),
                        "bbox": poligono,
                    }
                )

    return celdas


def _collect_lines(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    line_candidates: List[Any] = []
    for key_path in [
        ("data", "lines"),
        ("lines",),
        ("predictions",),
        ("items",),
        ("blocks",),
    ]:
        value = _dig(payload, *key_path)
        if isinstance(value, list):
            line_candidates.extend(value)

    resultados: List[Dict[str, Any]] = []
    for item in line_candidates:
        if not isinstance(item, dict):
            continue
        texto = item.get("text") or item.get("content") or item.get("value")
        if not texto:
            continue
        bbox = item.get("bbox") or item.get("polygon") or item.get("bounding_box")
        poligono = _normalize_polygon(bbox)
        if not poligono:
            continue
        confianza = item.get("confidence") or item.get("score") or 0.8
        resultados.append(
            {
                "text": texto,
                "confidence": float(confianza),
                "bbox": poligono,
            }
        )

    return resultados


def _to_parser_results(items: Iterable[Dict[str, Any]]) -> List[List[Any]]:
    resultados: List[List[Any]] = []
    for item in items:
        texto = item.get("text")
        bbox = item.get("bbox")
        confianza = item.get("confidence", 0.0)
        if not texto or not bbox:
            continue
        resultados.append([bbox, texto, float(confianza)])
    return resultados


def leer_tabla(imagen: ReadableImage) -> List[List[Any]]:
    """
    Ejecuta Chandra OCR y devuelve resultados compatibles con el parser de tablas.
    Cada elemento tiene formato [bbox, texto, confianza].
    """
    config = _get_config()
    imagen_bytes = _load_image_bytes(imagen)
    headers = _build_headers(config["api_key"])

    with httpx.Client(timeout=config["timeout"]) as client:
        payload = _post_ocr_request(
            client=client,
            api_url=config["api_url"],
            headers=headers,
            model=config["model"],
            image_bytes=imagen_bytes,
        )

    celdas = _collect_cells_from_tables(payload)
    if celdas:
        logger.info("Chandra OCR devolvió %d celdas con layout de tabla.", len(celdas))
        return _to_parser_results(celdas)

    lineas = _collect_lines(payload)
    if lineas:
        logger.info("Chandra OCR devolvió %d líneas sin layout de tabla.", len(lineas))
        return _to_parser_results(lineas)

    detalle = payload.keys()
    raise ChandraOcrResponseError(
        f"Chandra OCR no devolvió datos aprovechables. Claves presentes: {list(detalle)}"
    )
