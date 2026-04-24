import os
import logging

from dotenv import load_dotenv

load_dotenv()

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(format=LOG_FORMAT, level=LOG_LEVEL)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    TESTING = os.environ.get("TESTING", "false").lower() == "true"
    WTF_CSRF_ENABLED = os.environ.get("WTF_CSRF_ENABLED", "true").lower() == "true"
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

    # RAG 检索参数
    RAG_CHUNK_MIN = int(os.environ.get("RAG_CHUNK_MIN", "400"))
    RAG_CHUNK_MAX = int(os.environ.get("RAG_CHUNK_MAX", "1200"))
    RAG_CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "80"))
    RAG_CHUNK_TOP_K = int(os.environ.get("RAG_CHUNK_TOP_K", "12"))
    RAG_FACT_TOP_K = int(os.environ.get("RAG_FACT_TOP_K", "12"))
    RAG_CHUNK_SCORE_THRESHOLD = float(os.environ.get("RAG_CHUNK_SCORE_THRESHOLD", "0.35"))
    RAG_FACT_SCORE_THRESHOLD = float(os.environ.get("RAG_FACT_SCORE_THRESHOLD", "0.40"))
    RAG_MAX_CHUNKS_PER_FILE = int(os.environ.get("RAG_MAX_CHUNKS_PER_FILE", "2"))
    RAG_RRF_K = int(os.environ.get("RAG_RRF_K", "60"))
    RAG_ENABLE_BM25 = os.environ.get("RAG_ENABLE_BM25", "true").lower() == "true"
    RAG_BM25_TOP_K = int(os.environ.get("RAG_BM25_TOP_K", "20"))
    RAG_ENABLE_FACT_KEYWORD = os.environ.get("RAG_ENABLE_FACT_KEYWORD", "true").lower() == "true"
    RAG_FACT_KEYWORD_TOP_K = int(os.environ.get("RAG_FACT_KEYWORD_TOP_K", "100"))
    RAG_FACT_KEYWORD_MAX_RECORDS = int(os.environ.get("RAG_FACT_KEYWORD_MAX_RECORDS", "50000"))
    RAG_FACT_SEARCH_TEXT_CHARS = int(os.environ.get("RAG_FACT_SEARCH_TEXT_CHARS", "2000"))
    RAG_ENABLE_HYDE = os.environ.get("RAG_ENABLE_HYDE", "false").lower() == "true"
    RAG_CONTEXT_CHUNK_CHARS = int(os.environ.get("RAG_CONTEXT_CHUNK_CHARS", "700"))
    RAG_CONTEXT_EXPAND_NEIGHBORS = int(os.environ.get("RAG_CONTEXT_EXPAND_NEIGHBORS", "1"))

    # 生成层保护 (prompt guard / 对比模板 / 引用合法性后校验)
    RAG_ENABLE_PROMPT_GUARD = os.environ.get("RAG_ENABLE_PROMPT_GUARD", "true").lower() == "true"
    RAG_ENABLE_COMPARISON_TEMPLATE = os.environ.get("RAG_ENABLE_COMPARISON_TEMPLATE", "true").lower() == "true"
    RAG_COMPARISON_MIN_DIMENSIONS = int(os.environ.get("RAG_COMPARISON_MIN_DIMENSIONS", "3"))
    RAG_CITATION_POSTCHECK = os.environ.get("RAG_CITATION_POSTCHECK", "true").lower() == "true"
    RAG_CITATION_PENALTY = float(os.environ.get("RAG_CITATION_PENALTY", "0.25"))

    # /api/v1/search 自动路由（未指定 repo 时 LLM 在可见 KB 中选 1 个）
    RAG_ROUTE_MIN_CONFIDENCE = float(os.environ.get("RAG_ROUTE_MIN_CONFIDENCE", "0.5"))
    RAG_ROUTE_PRESELECT_LIMIT = int(os.environ.get("RAG_ROUTE_PRESELECT_LIMIT", "10"))

    # 摄入并发
    INGEST_LLM_CONCURRENCY = int(os.environ.get("INGEST_LLM_CONCURRENCY", "4"))
    INGEST_INDEX_CONCURRENCY = int(os.environ.get("INGEST_INDEX_CONCURRENCY", "4"))

    MINERU_API_URL = os.environ.get("MINERU_API_URL", "http://localhost:8000")
    MINERU_TIMEOUT = int(os.environ.get("MINERU_TIMEOUT", "300"))

    MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    SITE_NAME = os.environ.get("SITE_NAME", "Open LLM Wiki")
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")
    # IANA 时区名，用于页脚/模板/API 展示、查询追溯日志分卷、Wiki 内嵌日期等（库内仍存 UTC）
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Shanghai")

    MAIL_HOST = os.environ.get("MAIL_HOST", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "465"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "true").lower() == "true"
    MAIL_FROM = os.environ.get("MAIL_FROM", MAIL_USERNAME or "")
    PASSWORD_RESET_EXPIRES = int(os.environ.get("PASSWORD_RESET_EXPIRES", "3600"))
