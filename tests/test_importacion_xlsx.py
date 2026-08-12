"""Fase M: bug real encontrado probando Green Mills en dev -- pandas
necesita openpyxl para leer .xlsx y no estaba en requirements.txt. El
error real (ImportError de pandas) quedaba disfrazado como "Formato no
soportado" por el catch genérico, indistinguible de un archivo con
formato raro. Estos tests suben un .xlsx real (no CSV) contra los
endpoints de importación."""
import io
import uuid

import pandas as pd
from sqlmodel import select

from app.models.domain import Linea, MaestroSKU, Planta
from tests.conftest import autenticar_como


def _preparar_linea(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta XLSX")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea XLSX")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    return planta, linea


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return buf.read()


def test_subir_skus_xlsx_real_no_falla_por_formato(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)

    # codigo_sku sigue siendo PK legacy global (Fase C2 pendiente) -- usamos
    # un código único por corrida para no chocar con datos de corridas previas.
    codigo_sku = f"SKU-XLSX-1-{uuid.uuid4().hex[:8]}"
    df = pd.DataFrame({
        "codigo_sku": [codigo_sku],
        "descripcion": ["Producto de prueba"],
        "tiempo_ideal_seg": [240],
        "unidades_por_ciclo": [1],
    })
    contenido = _xlsx_bytes(df)

    r = client.post(
        "/api/lite/importaciones/skus/upload",
        data={"linea_id": str(linea.id)},
        files={"file": ("catalogo.xlsx", contenido, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["resultados"]["creados"] == 1

    sku_db = db.exec(
        select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo_sku, MaestroSKU.tenant_id == tenant_a)
    ).first()
    assert sku_db is not None
    assert sku_db.descripcion == "Producto de prueba"


def test_subir_plan_xlsx_real_no_falla_por_formato(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)

    # id_orden y codigo_sku siguen siendo PK legacy globales (Fase C2
    # pendiente) -- usamos valores únicos por corrida.
    codigo_sku = f"SKU-XLSX-2-{uuid.uuid4().hex[:8]}"
    id_orden = f"OP-XLSX-1-{uuid.uuid4().hex[:8]}"

    # El plan exige que el SKU ya exista -- lo damos de alta primero.
    sku = MaestroSKU(tenant_id=tenant_a, codigo_sku=codigo_sku, descripcion="Para el plan")
    db.add(sku)
    db.commit()

    df = pd.DataFrame({
        "id_orden": [id_orden],
        "sku_fk": [codigo_sku],
        "cantidad_esperada": [10],
        "plan_fecha": ["2026-12-31"],
    })
    contenido = _xlsx_bytes(df)

    r = client.post(
        "/api/lite/importaciones/plan/upload",
        data={"linea_id": str(linea.id)},
        files={"file": ("plan.xlsx", contenido, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["resultados"]["creadas"] == 1
