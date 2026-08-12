"""Fase Q (feedback de producto sobre la app en uso):

- Turno.dias_semana: CRUD vía /config/turnos/.

La cascada Estación > Línea > default de sistema (umbral_optimo/lento/
alerta en segundos, tolerancia_lento/alerta_pct en %) que Fase Q agregó
originalmente fue RETIRADA en Fase AC -- ver test_fase_ac_perfil_tiempos.py
para el modelo nuevo (SKU×Estación > SKU > Línea, siempre en segundos).
"""
from tests.conftest import autenticar_como


# ---------- Turno.dias_semana ----------

def test_crear_turno_sin_dias_semana_default_todos_los_dias(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/config/turnos/", json={"nombre": "Full", "hora_inicio": "06:00:00", "hora_fin": "14:00:00"})
    assert r.status_code == 201
    assert r.json()["dias_semana"] == "1,2,3,4,5,6,7"


def test_crear_turno_con_dias_semana_lu_vi(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/turnos/",
        json={"nombre": "Lu-Vi", "hora_inicio": "06:00:00", "hora_fin": "14:00:00", "dias_semana": [1, 2, 3, 4, 5]},
    )
    assert r.status_code == 201
    assert r.json()["dias_semana"] == "1,2,3,4,5"


def test_crear_turno_dia_invalido_devuelve_422(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/turnos/",
        json={"nombre": "Malo", "hora_inicio": "06:00:00", "hora_fin": "14:00:00", "dias_semana": [0, 9]},
    )
    assert r.status_code == 422


def test_actualizar_turno_dias_semana(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/config/turnos/", json={"nombre": "T", "hora_inicio": "06:00:00", "hora_fin": "14:00:00"})
    turno_id = r.json()["id"]

    r = client.patch(f"/config/turnos/{turno_id}", json={"dias_semana": [6, 7]})
    assert r.status_code == 200
    assert r.json()["dias_semana"] == "6,7"


def test_actualizar_turno_sin_mandar_dias_semana_no_lo_toca(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/turnos/",
        json={"nombre": "T", "hora_inicio": "06:00:00", "hora_fin": "14:00:00", "dias_semana": [1, 3, 5]},
    )
    turno_id = r.json()["id"]

    r = client.patch(f"/config/turnos/{turno_id}", json={"nombre": "T Renombrado"})
    assert r.status_code == 200
    assert r.json()["dias_semana"] == "1,3,5"  # no se tocó
