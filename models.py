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
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    email_verified_at = db.Column(db.DateTime, nullable=True)
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
    members = db.relationship(
        "RepoMember",
        back_populates="repo",
        cascade="all, delete-orphan",
    )
    share_codes = db.relationship(
        "RepoShareCode",
        back_populates="repo",
        cascade="all, delete-orphan",
    )


class RepoMember(db.Model):
    __tablename__ = "repo_members"
    __table_args__ = (
        UniqueConstraint("repo_id", "user_id", name="uq_repo_member_repo_user"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    role = db.Column(db.String(16), nullable=False, default="viewer")
    granted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    share_code_id = db.Column(db.Integer, db.ForeignKey("repo_share_codes.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)

    repo = db.relationship("Repo", back_populates="members")
    user = db.relationship("User", foreign_keys=[user_id], backref="repo_memberships")
    granted_by = db.relationship("User", foreign_keys=[granted_by_user_id])
    share_code = db.relationship("RepoShareCode", foreign_keys=[share_code_id], backref="members")


class RepoShareCode(db.Model):
    __tablename__ = "repo_share_codes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    code = db.Column(db.String(32), nullable=False, unique=True, index=True)
    role = db.Column(db.String(16), nullable=False, default="viewer")
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    use_count = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)

    repo = db.relationship("Repo", back_populates="share_codes")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


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
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    repo = db.relationship("Repo", back_populates="tasks")


class QueryLog(db.Model):
    __tablename__ = "query_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    trace_id = db.Column(db.String(36), nullable=True, unique=True, index=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    question = db.Column(db.Text, nullable=False)
    answer_preview = db.Column(db.Text, nullable=True)
    full_answer = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    confidence = db.Column(db.String(16), nullable=False, default="low")
    wiki_hit_count = db.Column(db.Integer, nullable=False, default=0)
    chunk_hit_count = db.Column(db.Integer, nullable=False, default=0)
    used_wiki_pages = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    used_chunk_ids = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    evidence_summary = db.Column(db.Text, nullable=True)
    retrieval_json = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    query_mode = db.Column(db.String(16), nullable=False, default="")
    latency_ms = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)


class ConversationSession(db.Model):
    """多轮对话会话（存储最近 N 轮消息）"""
    __tablename__ = "conversation_sessions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    session_key = db.Column(db.String(64), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False, default="新对话")
    messages_json = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False, default="[]")
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utc_now, onupdate=_utc_now)


class AuditLog(db.Model):
    """审计日志：登录/Token、知识库写操作、上传与摄入、检索（含流式/API）、Schema 等"""
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    username = db.Column(db.String(64), nullable=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    resource_type = db.Column(db.String(32), nullable=True)
    resource_id = db.Column(db.String(128), nullable=True)
    detail = db.Column(db.Text, nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now, index=True)


class ApiToken(db.Model):
    """API Token 机器凭证（供 OpenClaw / 脚本 / CI 等外部系统调用 /api/v1/**）。"""
    __tablename__ = "api_tokens"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    token_hash = db.Column(db.String(256), nullable=False, unique=True)
    # token_cipher 为 Fernet 对称加密后的明文，让列表页能"一键复制完整 token"；
    # 密钥在 .env 的 API_TOKEN_ENC_KEY，DB 本身不知道。未配置密钥或老记录为 NULL。
    token_cipher = db.Column(db.Text, nullable=True)
    token_prefix = db.Column(db.String(16), nullable=False, default="")
    scopes = db.Column(db.String(255), nullable=False, default="kb:search,kb:read")
    expires_at = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User", backref="api_tokens")

    def has_scope(self, scope: str) -> bool:
        """检查 token 是否具备指定 scope。"""
        parts = [s.strip() for s in (self.scopes or "").split(",") if s.strip()]
        return scope in parts

    def is_expired(self, now: datetime | None = None) -> bool:
        """过期判断：expires_at=None 表示永不过期。"""
        if self.expires_at is None:
            return False
        current = now or _utc_now()
        reference = self.expires_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current >= reference

    def is_usable(self, now: datetime | None = None) -> bool:
        return self.is_active and not self.is_expired(now)


class QueryFeedback(db.Model):
    __tablename__ = "query_feedbacks"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    trace_id = db.Column(db.String(36), nullable=False, index=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    rating = db.Column(db.String(8), nullable=False)  # "good" or "bad"
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))
