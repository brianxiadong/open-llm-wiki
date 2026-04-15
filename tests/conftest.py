"""Shared fixtures for the test suite.

Uses SQLite in-memory so tests run without MySQL.
All external services (LLM, MinerU, Qdrant, Embedding) are mocked.
"""

import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_API_BASE", "http://fake-llm")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("LLM_MAX_TOKENS", "1024")
os.environ.setdefault("EMBEDDING_API_BASE", "http://fake-embed")
os.environ.setdefault("EMBEDDING_API_KEY", "")
os.environ.setdefault("EMBEDDING_MODEL", "test-embed")
os.environ.setdefault("EMBEDDING_DIMENSIONS", "128")
os.environ.setdefault("QDRANT_URL", "http://fake-qdrant:6333")
os.environ.setdefault("MINERU_API_URL", "http://fake-mineru:8000")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_NAME", "test")


@pytest.fixture()
def tmp_data_dir():
    d = tempfile.mkdtemp(prefix="llmwiki_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def app(tmp_data_dir):
    """Create a Flask test application with SQLite and mocked services."""
    from config import Config

    Config.SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    Config.DATA_DIR = tmp_data_dir
    Config.TESTING = True
    Config.WTF_CSRF_ENABLED = False
    Config.SECRET_KEY = "test-secret"
    Config.APP_BASE_URL = "http://testserver"
    Config.MAIL_HOST = "smtp.test.local"
    Config.MAIL_PORT = 465
    Config.MAIL_USERNAME = "noreply@test.local"
    Config.MAIL_PASSWORD = "secret"
    Config.MAIL_USE_SSL = True
    Config.MAIL_FROM = "noreply@test.local"
    Config.PASSWORD_RESET_EXPIRES = 3600

    mock_qdrant_client = MagicMock()
    mock_qdrant_client.collection_exists.return_value = True
    mock_qdrant_client.get_collections.return_value = MagicMock(collections=[])

    with patch("qdrant_service.QdrantClient", return_value=mock_qdrant_client), \
         patch("qdrant_service.OpenAI") as mock_embed_openai, \
         patch("llm_client.OpenAI") as mock_llm_openai:

        mock_embed_resp = MagicMock()
        mock_embed_resp.data = [MagicMock(embedding=[0.1] * 128)]
        mock_embed_resp.usage = MagicMock(total_tokens=10)
        mock_embed_openai.return_value.embeddings.create.return_value = mock_embed_resp

        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [MagicMock(message=MagicMock(content='{"test": true}'))]
        mock_chat_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_llm_openai.return_value.chat.completions.create.return_value = mock_chat_resp

        from app import create_app
        application = create_app()

        with application.app_context():
            from models import db
            db.create_all()

        yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def runner(app):
    return app.test_cli_runner()


@pytest.fixture()
def auth_client(client, app):
    """A test client that is already logged in as 'alice'."""
    with app.app_context():
        from models import User, db
        user = User(username="alice", email="alice@example.com", display_name="Alice")
        user.set_password("password123")
        user.email_verified = True
        db.session.add(user)
        db.session.commit()

    client.post("/login", data={"username": "alice", "password": "password123"})
    return client


@pytest.fixture()
def sample_repo(auth_client, app):
    """Create a sample repo via the web flow and return (client, repo_info)."""
    auth_client.post("/repos/new", data={
        "name": "Test KB",
        "slug": "test-kb",
        "description": "A test knowledge base",
    })
    with app.app_context():
        from models import Repo
        repo = Repo.query.filter_by(slug="test-kb").first()
    return auth_client, {"username": "alice", "slug": "test-kb", "id": repo.id if repo else None}
