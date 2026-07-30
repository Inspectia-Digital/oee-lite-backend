"""Fase K (auditoría QA #7 y #8): límite de tamaño/filas en importaciones
y sanitización de errores internos filtrados al cliente."""
import io

import pandas as pd

from app.models.domain import Linea, Planta
from app.routers.importaciones import MAX_UPLOAD_BYTES
from tests.conftest import autenticar_como


def _preparar_linea(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Import")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Import")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    return planta, linea


def test_archivo_demasiado_grande_devuelve_413(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)

    contenido_enorme = b"a" * (MAX_UPLOAD_BYTES + 1024)
    r = client.post(
        "/api/lite/importaciones/plan/upload",
        data={"linea_id": str(linea.id)},
        files={"file": ("plan.csv", contenido_enorme, "text/csv")},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 413


def test_archivo_con_demasiadas_filas_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)

    df = pd.DataFrame({
        "id_orden": [f"OP-{i}" for i in range(20_001)],
        "sku_fk": ["SKU-1"] * 20_001,
        "cantidad_esperada": [1] * 20_001,
        "plan_fecha": ["2026-01-01"] * 20_001,
    })
    buffer = io.BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)

    r = client.post(
        "/api/lite/importaciones/plan/upload",
        data={"linea_id": str(linea.id)},
        files={"file": ("plan.csv", buffer, "text/csv")},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400
    assert "20001" in r.json()["detail"] or "20,001" in r.json()["detail"]


def test_error_de_fila_no_filtra_texto_crudo_de_excepcion(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)

    # cantidad_esperada no numérica -> dispara una excepción al hacer
    # int(...) dentro del procesamiento de la fila.
    contenido = b"id_orden,sku_fk,cantidad_esperada,plan_fecha\nOP-1,SKU-1,no-es-un-numero,2026-01-01\n"
    r = client.post(
        "/api/lite/importaciones/plan/upload",
        data={"linea_id": str(linea.id)},
        files={"file": ("plan.csv", contenido, "text/csv")},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    errores = r.json()["resultados"]["errores"]
    assert len(errores) == 1
    # El mensaje no debe filtrar el texto crudo de la excepción de Python
    # (ej. "invalid literal for int() with base 10: 'no-es-un-numero'").
    assert "invalid literal" not in errores[0]
    assert "no-es-un-numero" not in errores[0]
