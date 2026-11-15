"""Integration tests use a unique disposable PostgreSQL database, never the app database."""

import os
from uuid import uuid4

import psycopg
import pymupdf
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, text


@pytest.fixture(scope="session")
def runtime(tmp_path_factory):
    from documind.config import settings
    from documind.db import engine
    from documind.storage import store

    cfg = settings()
    url = create_engine(cfg.database_url.get_secret_value()).url
    name = "documind_test_" + uuid4().hex
    admin_url = (
        url.set(database="postgres")
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://")
    )
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    changes = {
        "DATABASE_URL": url.set(database=name).render_as_string(hide_password=False),
        "OBJECT_STORE_ROOT": str(tmp_path_factory.mktemp("objects")),
        "CELERY_QUEUE": name,
    }
    old = {k: os.environ.get(k) for k in changes}
    os.environ.update(changes)
    settings.cache_clear()
    engine.cache_clear()
    store.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine().dispose()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    settings.cache_clear()
    engine.cache_clear()
    store.cache_clear()
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def repo(runtime):
    from documind.config import settings
    from documind.db import engine
    from documind.repository import Repository
    from documind.storage import store

    with engine().begin() as c:
        c.execute(text("TRUNCATE workspaces CASCADE"))
    result = Repository(engine(), store(), settings())
    result.initialize()
    return result


@pytest.fixture
def client(repo):
    from documind.api import app

    with TestClient(
        app, headers={"Authorization": "Bearer " + repo.cfg.local_api_token.get_secret_value()}
    ) as c:
        yield c


@pytest.fixture
def pdf():
    def make(text="Grand total: INR 1,100.00", rotation=0, blank=False):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=400)
            if not blank:
                page.insert_text((40, 60), text)
            page.set_rotation(rotation)
            return doc.tobytes()

    return make


@pytest.fixture
def table_pdf():
    def make(scanned=False, rotation=0):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=300)
            for x in (30, 150, 270):
                page.draw_line((x, 60), (x, 180))
            for y in (60, 100, 140, 180):
                page.draw_line((30, y), (270, y))
            for x, y, text in (
                (40, 85, "Item"),
                (160, 85, "Amount"),
                (40, 125, "Pens"),
                (160, 125, "12.50"),
                (40, 165, "Paper"),
            ):
                page.insert_text((x, y), text, fontsize=14)
            if scanned:
                png = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
                with pymupdf.open() as scan:
                    p = scan.new_page(width=300, height=300)
                    p.insert_image(p.rect, stream=png)
                    p.set_rotation(rotation)
                    return scan.tobytes()
            page.set_rotation(rotation)
            return doc.tobytes()

    return make
