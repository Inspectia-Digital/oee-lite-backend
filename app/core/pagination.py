"""Fase BU (auditoría de robustez, batch 3): helper compartido para
paginación opcional en endpoints de catálogo (SKUs, órdenes, planes,
operarios, supervisores) que hoy devuelven la lista entera siempre, sin
límite -- crecen indefinidamente con el tiempo.

`limit` es deliberadamente OPCIONAL (no un default bajo tipo 100): varios
consumidores del frontend de hoy (selects/dropdowns para elegir un SKU o
una línea al cargar una orden) necesitan la lista COMPLETA para buscar
sobre ella, no una página -- ponerle un límite por defecto los rompería en
silencio apenas un tenant creciera más allá de esa página. Sólo trunca
cuando el caller pide `limit` explícitamente (ver Fase BV: las vistas de
tabla grande del frontend, EntityTable y afines, son las que empiezan a
pedirlo).

Mismo criterio de validación que ya usaba `/supervisor/paradas`
(operacion.py::listar_historial_paradas) -- centralizado acá para no
repetirlo en cada router nuevo.
"""
from typing import Optional, TypeVar

from fastapi import HTTPException

T = TypeVar("T")

LIMIT_MAXIMO = 500


def aplicar_paginacion(query: T, limit: Optional[int], offset: int) -> T:
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset no puede ser negativo.")
    if limit is None:
        return query
    if not (1 <= limit <= LIMIT_MAXIMO):
        raise HTTPException(status_code=400, detail=f"limit debe estar entre 1 y {LIMIT_MAXIMO}.")
    return query.offset(offset).limit(limit)
