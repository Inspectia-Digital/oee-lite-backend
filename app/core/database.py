from sqlmodel import create_engine, Session
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_pre_ping=True,
)

def get_session():
    """Generador de sesiones para inyectar en los endpoints de FastAPI."""
    with Session(engine) as session:
        yield session