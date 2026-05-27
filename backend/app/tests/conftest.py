import os

import pytest

os.environ["TRADER_DATABASE_URL"] = "sqlite:///./runtime/test_trader_app.db"
os.environ["TRADER_EMBED_WORKER"] = "false"

from backend.app.db.session import Base, SessionLocal, engine  # noqa: E402
from backend.app.db import models  # noqa: E402,F401


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
