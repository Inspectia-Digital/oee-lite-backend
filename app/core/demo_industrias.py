"""Fase FA (Ambiente Demo): catálogo de industrias para orientar los
datos que se generan al crear una demo -- pedido explícito del usuario
("antes preguntar en el front por industria... para orientar la demo a
lo que se necesita"). Deliberadamente un dict hardcoded, no una tabla:
es la parametrización "base" del MVP (elegir industria cambia nombres/
datos, no un formulario libre de diseño de plantas) -- agregar una
industria nueva es editar este archivo, no una migración.

Cada entrada define: nombre de la línea, tipo de producción (afecta
cómo se interpretan las unidades -- ver Linea.tipo_produccion), y una
lista de SKUs de ejemplo (código, descripción, tiempo_ideal_seg
realista para el rubro).
"""
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class SkuDemo:
    codigo: str
    descripcion: str
    tiempo_ideal_seg: float
    unidades_por_ciclo: int = 1


@dataclass(frozen=True)
class IndustriaDemo:
    label: str
    nombre_linea: str
    tipo_produccion: str  # "discreta" | "por_lotes" -- ver TipoProduccion
    nombre_estacion: str
    skus: List[SkuDemo] = field(default_factory=list)


INDUSTRIAS_DEMO = {
    "textil": IndustriaDemo(
        label="Textil",
        nombre_linea="Línea de Tejeduría",
        tipo_produccion="discreta",
        nombre_estacion="Telar Principal",
        skus=[
            SkuDemo("TEX-ALG-180", "Tela de Algodón 180g/m²", tiempo_ideal_seg=45.0),
            SkuDemo("TEX-POL-220", "Tela de Poliéster 220g/m²", tiempo_ideal_seg=38.0),
        ],
    ),
    "alimenticia": IndustriaDemo(
        label="Alimenticia",
        nombre_linea="Línea de Envasado",
        tipo_produccion="por_lotes",
        nombre_estacion="Envasadora Automática",
        skus=[
            SkuDemo("ALM-GALL-200", "Paquete Galletitas 200g", tiempo_ideal_seg=8.0, unidades_por_ciclo=6),
            SkuDemo("ALM-HAR-1KG", "Bolsa de Harina 1kg", tiempo_ideal_seg=6.5, unidades_por_ciclo=4),
        ],
    ),
    "automotriz": IndustriaDemo(
        label="Automotriz",
        nombre_linea="Línea de Ensamble",
        tipo_produccion="discreta",
        nombre_estacion="Estación de Torque",
        skus=[
            SkuDemo("AUT-BUJE-STD", "Buje Estándar Suspensión", tiempo_ideal_seg=22.0),
            SkuDemo("AUT-SOP-MOT", "Soporte de Motor", tiempo_ideal_seg=31.0),
        ],
    ),
    "metalurgica": IndustriaDemo(
        label="Metalúrgica",
        nombre_linea="Línea de Estampado",
        tipo_produccion="discreta",
        nombre_estacion="Prensa Hidráulica",
        skus=[
            SkuDemo("MET-CHAP-2MM", "Chapa Estampada 2mm", tiempo_ideal_seg=15.0),
            SkuDemo("MET-PERF-STD", "Pieza Perfilada Estándar", tiempo_ideal_seg=19.5),
        ],
    ),
}

INDUSTRIAS_VALIDAS = tuple(INDUSTRIAS_DEMO.keys())
