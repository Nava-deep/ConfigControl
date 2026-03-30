from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db.models import Base


@dataclass
class Database:
    engine: object
    session_factory: sessionmaker[Session]

    def session(self) -> Session:
        return self.session_factory()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def ping(self) -> bool:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True

    def dispose(self) -> None:
        self.engine.dispose()


def build_database(settings: Settings) -> Database:
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(settings.database_url, future=True, pool_pre_ping=True, connect_args=connect_args)
    return Database(engine=engine, session_factory=sessionmaker(bind=engine, expire_on_commit=False))
