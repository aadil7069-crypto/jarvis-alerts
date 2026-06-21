from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.schema import Base


def init_database(config: dict) -> sessionmaker:
    """
    Create the database engine, build all tables, and return a session factory.
    Each agent must call factory() to get its own isolated session — never share one session
    across agents, as concurrent commits cause lock errors on SQLite.
    """
    url = config.get("database", {}).get("url", "sqlite:///jarvis.db")
    engine = create_engine(url, echo=False, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)
