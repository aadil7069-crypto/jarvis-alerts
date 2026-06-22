from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models.schema import Base

# Columns added after initial schema creation — added via ALTER TABLE so existing
# databases don't lose data on upgrade.
_MIGRATIONS = [
    "ALTER TABLE trades ADD COLUMN high_price REAL",
    "ALTER TABLE trades ADD COLUMN tx_signature TEXT",
]


def _run_migrations(engine) -> None:
    with engine.connect() as conn:
        for stmt in _MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                # Column already exists — safe to ignore
                conn.rollback()


def init_database(config: dict) -> sessionmaker:
    """
    Create the database engine, build all tables, and return a session factory.
    Each agent must call factory() to get its own isolated session — never share one session
    across agents, as concurrent commits cause lock errors on SQLite.
    """
    url = config.get("database", {}).get("url", "sqlite:///jarvis.db")
    engine = create_engine(url, echo=False, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    _run_migrations(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)
