from app.core.database import engine
from sqlmodel import Session, select
from app.models.domain import UsuarioSaaS, RolUsuario
import uuid

# --- TUS CREDENCIALES ---
ID_HUMANO_GOOGLE = "google-oauth2|103641955647524616968"  # El que sacas de tu frontend/Auth0 Dashboard
ID_MAQUINA_M2M = "lvN3tWT4T1Zbi7qi0aAm7fdPZ5ZOCdfo@clients" # El que sacaste de la pestaña Test de Auth0

def organizar_arquitectura_saas():
    with Session(engine) as session:
        # 1. Configurar al Humano (InspectIA Core)
        humano = session.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == ID_HUMANO_GOOGLE)).first()
        if humano:
            humano.tenant_id = "tymeo_core"
            humano.rol = RolUsuario.SUPERADMIN
        else:
            session.add(UsuarioSaaS(id=uuid.uuid4(), auth0_id=ID_HUMANO_GOOGLE, tenant_id="tymeo_core", rol=RolUsuario.SUPERADMIN, activo=True))
            
        # 2. Configurar a la Máquina (Cliente New Garden)
        maquina = session.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == ID_MAQUINA_M2M)).first()
        if maquina:
            maquina.tenant_id = "new_garden"
            maquina.rol = RolUsuario.PRODUCCION # Un rol menor, es solo una máquina
        else:
            session.add(UsuarioSaaS(id=uuid.uuid4(), auth0_id=ID_MAQUINA_M2M, tenant_id="new_garden", rol=RolUsuario.PRODUCCION, activo=True))
            
        session.commit()
        print("✅ Arquitectura SaaS alineada: Humano en 'tymeo_core' | Máquina en 'new_garden'")

if __name__ == "__main__":
    organizar_arquitectura_saas()