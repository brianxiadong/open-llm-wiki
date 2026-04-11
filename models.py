from datetime import datetime, timezone

from flask_login import LoginManager, UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()
login_manager = LoginManager()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Repo(db.Model):
    __tablename__ = "repos"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_repo_user_slug"),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    slug = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utc_now, onupdate=_utc_now)
    source_count = db.Column(db.Integer, nullable=False, default=0)
    page_count = db.Column(db.Integer, nullable=False, default=0)
    is_public = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User", backref="repos")
    tasks = db.relationship("Task", back_populates="repo")


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued")
    input_data = db.Column(db.Text, nullable=True)
    output_data = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    progress = db.Column(db.Integer, nullable=False, default=0)
    progress_msg = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    repo = db.relationship("Repo", back_populates="tasks")


class QueryLog(db.Model):
    __tablename__ = "query_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    answer_preview = db.Column(db.Text, nullable=True)
    confidence = db.Column(db.String(16), nullable=False, default="low")
    wiki_hit_count = db.Column(db.Integer, nullable=False, default=0)
    chunk_hit_count = db.Column(db.Integer, nullable=False, default=0)
    used_wiki_pages = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    used_chunk_ids = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    evidence_summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))
