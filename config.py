import os
import logging

from dotenv import load_dotenv

load_dotenv()

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(format=LOG_FORMAT, level=LOG_LEVEL)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    DATA_DIR = os.environ.get("DATA_DIR", "./data")

    _db_user = os.environ.get("DB_USER", "root")
    _db_pass = os.environ.get("DB_PASSWORD", "")
    _db_host = os.environ.get("DB_HOST", "127.0.0.1")
    _db_port = os.environ.get("DB_PORT", "3306")
    _db_name = os.environ.get("DB_NAME", "llmwiki")
    SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{_db_user}:{_db_pass}@{_db_host}:{_db_port}/{_db_name}?charset=utf8mb4"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
    LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o")
    LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "16384"))

    EMBEDDING_API_BASE = os.environ.get("EMBEDDING_API_BASE", "")
    EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
    EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1024"))

    QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

    MINERU_API_URL = os.environ.get("MINERU_API_URL", "http://localhost:8000")
    MINERU_TIMEOUT = int(os.environ.get("MINERU_TIMEOUT", "300"))

    MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    SITE_NAME = os.environ.get("SITE_NAME", "Open LLM Wiki")
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")

    MAIL_HOST = os.environ.get("MAIL_HOST", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "465"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "true").lower() == "true"
    MAIL_FROM = os.environ.get("MAIL_FROM", MAIL_USERNAME or "")
    PASSWORD_RESET_EXPIRES = int(os.environ.get("PASSWORD_RESET_EXPIRES", "3600"))
