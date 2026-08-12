"""Fase AB (revisión completa de la cascada de umbrales/tolerancias a
pedido de Green Mills): ni Linea/Estacion (Create y Update) ni Tenant
(PATCH /mi-empresa/tenant) validaban que 'lento' fuera menor que
'alerta'. Si quedan invertidos, scans.py evalúa "delta_t > t_alerta"
ANTES que "delta_t > t_lento" -- con alerta más chico que lento, el
nivel LENTO queda matemáticamente inalcanzable, en silencio.
"""
from app.models.domain import Planta
from tests.conftest import autenticar_como


def _crear_planta(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AB Umbrales")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    return planta


def test_crear_linea_umbral_lento_mayor_que_alerta_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Invertida", "umbral_lento": 300, "umbral_alerta": 200},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400
    assert "lento" in r.json()["detail"].lower()


def test_crear_linea_umbral_lento_igual_a_alerta_devuelve_400(client, db, tenant_a, gerente_a):
    """Igual también es inválido -- '> t_alerta' y '> t_lento' con el
    mismo valor numérico deja a LENTO sin ningún delta_t que lo alcance."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Igual", "umbral_lento": 250, "umbral_alerta": 250},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_crear_linea_tolerancia_lento_mayor_que_alerta_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Tol Invertida", "tolerancia_lento_pct": 1.30, "tolerancia_alerta_pct": 1.15},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400
    assert "lento" in r.json()["detail"].lower()


def test_crear_linea_umbrales_correctos_funciona(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea OK", "umbral_lento": 200, "umbral_alerta": 300},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201


def test_crear_linea_solo_un_valor_definido_no_valida(client, db, tenant_a, gerente_a):
    """Sin los dos valores presentes no hay nada que comparar -- no debe
    bloquear la carga incremental (cargar sólo 'lento' hoy, 'alerta' en
    otro momento)."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Parcial", "umbral_lento": 200},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201


def test_actualizar_linea_a_umbral_invertido_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea a Invertir", "umbral_lento": 200, "umbral_alerta": 300},
        headers=headers,
    )
    linea_id = r.json()["id"]

    r = client.patch(f"/config/lineas/{linea_id}", json={"umbral_lento": 350}, headers=headers)
    assert r.status_code == 400


def test_crear_estacion_umbral_lento_mayor_que_alerta_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    linea = client.post("/config/lineas/", json={"nombre": "Línea E"}, headers=headers).json()
    r = client.post(
        "/config/estaciones/",
        json={
            "nombre": "Estación Invertida", "tipo": "sensor", "linea_id": linea["id"],
            "umbral_lento": 300, "umbral_alerta": 200,
        },
        headers=headers,
    )
    assert r.status_code == 400


def test_actualizar_estacion_tolerancia_invertida_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    linea = client.post("/config/lineas/", json={"nombre": "Línea E2"}, headers=headers).json()
    est = client.post(
        "/config/estaciones/",
        json={"nombre": "Estación E2", "tipo": "sensor", "linea_id": linea["id"]},
        headers=headers,
    ).json()

    r = client.patch(
        f"/config/estaciones/{est['id']}",
        json={"tolerancia_lento_pct": 1.30, "tolerancia_alerta_pct": 1.15},
        headers=headers,
    )
    assert r.status_code == 400


def test_actualizar_tenant_tolerancia_invertida_devuelve_400(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.patch(
        "/accesos/mi-empresa/tenant",
        json={"tolerancia_lento_pct": 1.30, "tolerancia_alerta_pct": 1.15},
    )
    assert r.status_code == 400


def test_actualizar_tenant_tolerancia_correcta_funciona(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.patch(
        "/accesos/mi-empresa/tenant",
        json={"tolerancia_lento_pct": 1.15, "tolerancia_alerta_pct": 1.25},
    )
    assert r.status_code == 200
