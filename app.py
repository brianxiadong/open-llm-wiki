import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone

from flask import (
    Flask,
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename

from config import Config
from exceptions import MineruClientError, QdrantServiceError
from llm_client import LLMClient
from mineru_client import MineruClient
from models import Repo, Task, User, db, login_manager
from qdrant_service import QdrantService
from task_worker import TaskWorker
from utils import (
    DEFAULT_SCHEMA_MD,
    ensure_repo_dirs,
    extract_links,
    get_backlinks,
    get_repo_path,
    list_raw_sources,
    list_wiki_pages,
    render_markdown,
    slugify,
)
from wiki_engine import WikiEngine

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"md", "txt", "pdf", "doc", "docx", "pptx", "png", "jpg", "jpeg"}
MINERU_EXTENSIONS = {"pdf", "doc", "docx", "pptx", "png", "jpg", "jpeg"}
TYPE_LABELS = {
    "concept": "概念",
    "guide": "指南",
    "reference": "参考",
    "overview": "概览",
    "comparison": "对比",
    "log": "日志",
    "index": "索引",
    "source": "来源",
    "entity": "实体",
    "analysis": "分析",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _file_ext(filename: str) -> str:
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


def _get_repo_or_404(username: str, repo_slug: str) -> tuple:
    user = User.query.filter_by(username=username).first_or_404()
    repo = Repo.query.filter_by(user_id=user.id, slug=repo_slug).first_or_404()
    return user, repo


def _require_owner(repo: Repo) -> None:
    if not current_user.is_authenticated or current_user.id != repo.user_id:
        abort(403)


def _is_owner(repo: Repo) -> bool:
    return current_user.is_authenticated and current_user.id == repo.user_id


def _enrich_sources(raw_dir: str, repo: Repo) -> list[dict]:
    sources = list_raw_sources(raw_dir)
    ingested_files: set[str] = set()
    active_tasks: dict[str, dict] = {}
    for t in Task.query.filter_by(repo_id=repo.id, type="ingest").all():
        if t.input_data:
            if t.status == "done":
                ingested_files.add(t.input_data)
            elif t.status in ("queued", "running"):
                active_tasks[t.input_data] = {
                    "status": t.status,
                    "progress": t.progress or 0,
                    "progress_msg": t.progress_msg or "",
                    "task_id": t.id,
                }

    for s in sources:
        s["id"] = s["filename"]
        kb = s["size_kb"]
        s["size_display"] = f"{kb:.1f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"
        s["ingested"] = s["filename"] in ingested_files
        task_info = active_tasks.get(s["filename"], {})
        s["task_status"] = task_info.get("status", "")
        s["task_progress"] = task_info.get("progress", 0)
        s["task_progress_msg"] = task_info.get("progress_msg", "")
        s["task_id"] = task_info.get("task_id", 0)
        filepath = os.path.join(raw_dir, s["filename"])
        try:
            mtime = os.path.getmtime(filepath)
            s["created_at"] = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except OSError:
            s["created_at"] = None
    return sources


def _enrich_pages(wiki_dir: str) -> list[dict]:
    pages = list_wiki_pages(wiki_dir)
    for p in pages:
        p["slug"] = p["filename"].replace(".md", "")
    return pages


def _sync_repo_counts(repo: Repo, username: str) -> None:
    base = get_repo_path(Config.DATA_DIR, username, repo.slug)
    repo.source_count = len(list_raw_sources(os.path.join(base, "raw")))
    repo.page_count = len(list_wiki_pages(os.path.join(base, "wiki")))
    db.session.commit()


def _purge_source_wiki(app, repo, username: str, source_filename: str) -> int:
    """Delete wiki pages derived from source_filename and their Qdrant vectors.

    Returns the number of wiki pages removed.
    """
    from utils import render_markdown
    base = get_repo_path(Config.DATA_DIR, username, repo.slug)
    wiki_dir = os.path.join(base, "wiki")
    if not os.path.isdir(wiki_dir):
        return 0

    removed = 0
    stem = os.path.splitext(source_filename)[0].lower()
    for fname in os.listdir(wiki_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(wiki_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            fm, _ = render_markdown(content)
            page_source = fm.get("source", "")
            # Match by source frontmatter field or filename prefix
            if (page_source and (
                    page_source == source_filename
                    or os.path.splitext(page_source)[0].lower() == stem)
                ) or fname.startswith(stem + "-") or fname == stem + ".md":
                os.remove(fpath)
                try:
                    app.qdrant.delete_page(repo.id, fname)
                except Exception:
                    pass
                removed += 1
                logger.info("Purged wiki page %s (source: %s)", fname, source_filename)
        except Exception:
            pass
    return removed


def _wiki_base_url(username: str, repo_slug: str) -> str:
    raw = url_for(
        "wiki.view_page",
        username=username,
        repo_slug=repo_slug,
        page_slug="__PLACEHOLDER__",
    )
    return raw.replace("__PLACEHOLDER__", "").rstrip("/")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_SIZE

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "请先登录"

    app.llm = LLMClient(
        Config.LLM_API_BASE,
        Config.LLM_API_KEY,
        Config.LLM_MODEL,
        Config.LLM_MAX_TOKENS,
    )
    app.mineru = MineruClient(Config.MINERU_API_URL, Config.MINERU_TIMEOUT)
    try:
        app.qdrant = QdrantService(
            Config.QDRANT_URL,
            Config.EMBEDDING_API_BASE,
            Config.EMBEDDING_API_KEY,
            Config.EMBEDDING_MODEL,
            Config.EMBEDDING_DIMENSIONS,
        )
    except Exception:
        app.qdrant = None
        logging.warning("Qdrant service unavailable, running in degraded mode")
    app.wiki_engine = WikiEngine(app.llm, app.qdrant, Config.DATA_DIR)

    _register_routes(app)
    _register_error_handlers(app)

    os.makedirs(Config.DATA_DIR, exist_ok=True)

    if not app.config.get("TESTING"):
        worker = TaskWorker(app)
        worker.start()
        app.task_worker = worker

    return app


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("errors/500.html"), 500


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _register_routes(app: Flask) -> None:

    # ── Root ──────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(
                url_for("repo.list_repos", username=current_user.username)
            )
        return redirect(url_for("auth.login"))

    @app.route("/health")
    def health():
        checks: dict[str, str] = {}

        try:
            db.session.execute(db.text("SELECT 1"))
            checks["mysql"] = "ok"
        except Exception as exc:
            checks["mysql"] = f"error: {exc}"

        if app.qdrant:
            try:
                app.qdrant._qdrant.get_collections()
                checks["qdrant"] = "ok"
            except Exception as exc:
                checks["qdrant"] = f"error: {exc}"
        else:
            checks["qdrant"] = "unavailable"

        try:
            checks["mineru"] = "ok" if app.mineru.health_check() else "error"
        except Exception as exc:
            checks["mineru"] = f"error: {exc}"

        if app.qdrant:
            try:
                app.qdrant._embed("health-check")
                checks["embedding"] = "ok"
            except Exception as exc:
                checks["embedding"] = f"error: {exc}"
        else:
            checks["embedding"] = "unavailable"

        all_ok = all(v == "ok" for v in checks.values())
        return (
            jsonify(status="ok" if all_ok else "degraded", checks=checks),
            200 if all_ok else 503,
        )

    # ── Auth ──────────────────────────────────────────────────────────

    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(
                url_for("repo.list_repos", username=current_user.username)
            )
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                login_user(user)
                next_url = request.args.get("next")
                return redirect(
                    next_url
                    or url_for("repo.list_repos", username=user.username)
                )
            flash("用户名或密码错误", "error")
        return render_template("auth/login.html")

    @auth_bp.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(
                url_for("repo.list_repos", username=current_user.username)
            )
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            display_name = request.form.get("display_name", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")

            if not username or not password:
                flash("用户名和密码不能为空", "error")
                return render_template("auth/register.html")
            if not re.match(r"^[a-zA-Z0-9_]+$", username):
                flash("用户名只能包含字母、数字和下划线", "error")
                return render_template("auth/register.html")
            if len(password) < 8:
                flash("密码长度不能少于 8 位", "error")
                return render_template("auth/register.html")
            if password != confirm:
                flash("两次输入的密码不一致", "error")
                return render_template("auth/register.html")
            if User.query.filter_by(username=username).first():
                flash("用户名已存在", "error")
                return render_template("auth/register.html")

            user = User(username=username, display_name=display_name or None)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            login_user(user)
            flash("注册成功", "success")
            return redirect(
                url_for("repo.list_repos", username=user.username)
            )
        return render_template("auth/register.html")

    @auth_bp.route("/logout")
    def logout():
        logout_user()
        return redirect(url_for("auth.login"))

    app.register_blueprint(auth_bp)

    # ── User ──────────────────────────────────────────────────────────

    user_bp = Blueprint("user", __name__, url_prefix="/user")

    @user_bp.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        if request.method == "POST":
            action = request.form.get("action")
            if action == "update_profile":
                current_user.display_name = (
                    request.form.get("display_name", "").strip() or None
                )
                db.session.commit()
                flash("个人信息已更新", "success")
            elif action == "change_password":
                old_pw = request.form.get("old_password", "")
                new_pw = request.form.get("new_password", "")
                confirm = request.form.get("confirm_password", "")
                if not current_user.check_password(old_pw):
                    flash("当前密码错误", "error")
                elif new_pw != confirm:
                    flash("两次输入的新密码不一致", "error")
                elif len(new_pw) < 8:
                    flash("新密码长度不能少于 8 位", "error")
                else:
                    current_user.set_password(new_pw)
                    db.session.commit()
                    flash("密码修改成功", "success")
            return redirect(url_for("user.settings"))
        return render_template("user/settings.html")

    app.register_blueprint(user_bp)

    # ── Repo ──────────────────────────────────────────────────────────

    repo_bp = Blueprint("repo", __name__)

    @repo_bp.route("/<username>")
    def list_repos(username):
        user = User.query.filter_by(username=username).first_or_404()
        repos = (
            Repo.query.filter_by(user_id=user.id)
            .order_by(Repo.updated_at.desc())
            .all()
        )
        # Attach active task count to each repo for the card UI
        for repo in repos:
            repo.active_task_count = Task.query.filter(
                Task.repo_id == repo.id,
                Task.status.in_(["queued", "running"]),
            ).count()
        return render_template("repo/list.html", username=username, repos=repos)

    @repo_bp.route("/repos/new", methods=["GET", "POST"])
    @login_required
    def new_repo():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            slug_raw = request.form.get("slug", "").strip()
            description = request.form.get("description", "").strip()

            if not name:
                flash("知识库名称不能为空", "error")
                return render_template("repo/new.html")

            slug = slug_raw or slugify(name)
            if not slug:
                flash("无法生成有效的 URL 标识", "error")
                return render_template("repo/new.html")

            if Repo.query.filter_by(user_id=current_user.id, slug=slug).first():
                flash("该标识已被使用", "error")
                return render_template("repo/new.html")

            repo = Repo(
                user_id=current_user.id,
                name=name,
                slug=slug,
                description=description,
            )
            db.session.add(repo)
            db.session.commit()

            base = ensure_repo_dirs(Config.DATA_DIR, current_user.username, slug)
            wiki_dir = os.path.join(base, "wiki")
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            with open(os.path.join(wiki_dir, "schema.md"), "w", encoding="utf-8") as f:
                f.write(DEFAULT_SCHEMA_MD)

            with open(os.path.join(wiki_dir, "index.md"), "w", encoding="utf-8") as f:
                f.write(
                    f"---\ntitle: 首页\ntype: index\nupdated: {now_str}\n---\n\n"
                    f"# {name}\n\n暂无内容，请上传文档并摄入。\n"
                )

            with open(os.path.join(wiki_dir, "log.md"), "w", encoding="utf-8") as f:
                f.write(
                    "---\ntitle: Ingestion Log\ntype: log\n---\n\n"
                    f"# Ingestion Log\n\n- {now_str}: 知识库创建\n"
                )

            with open(os.path.join(wiki_dir, "overview.md"), "w", encoding="utf-8") as f:
                f.write(
                    f"---\ntitle: 概览\ntype: overview\nupdated: {now_str}\n---\n\n"
                    f"# {name} 概览\n\n"
                    "暂无概览内容。上传文档并摄入后，此页面将自动更新。\n"
                )

            if app.qdrant:
                try:
                    app.qdrant.ensure_collection(repo.id)
                except Exception:
                    logger.warning(
                        "Failed to create Qdrant collection for repo %s", repo.id
                    )

            _sync_repo_counts(repo, current_user.username)
            flash("知识库创建成功", "success")
            return redirect(
                url_for(
                    "repo.dashboard",
                    username=current_user.username,
                    repo_slug=slug,
                )
            )
        return render_template("repo/new.html")

    @repo_bp.route("/<username>/<repo_slug>")
    def dashboard(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        base = get_repo_path(Config.DATA_DIR, username, repo_slug)
        wiki_dir = os.path.join(base, "wiki")

        pages = _enrich_pages(wiki_dir)

        page_content = None
        active_page = None
        overview_path = os.path.join(wiki_dir, "overview.md")
        if os.path.isfile(overview_path):
            with open(overview_path, "r", encoding="utf-8") as f:
                raw = f.read()
            _, page_content = render_markdown(raw, _wiki_base_url(username, repo_slug))
            for p in pages:
                if p["slug"] == "overview":
                    active_page = p
                    break

        raw_dir = os.path.join(base, "raw")
        sources = _enrich_sources(raw_dir, repo) if os.path.isdir(raw_dir) else []

        return render_template(
            "repo/dashboard.html",
            username=username,
            repo=repo,
            pages=pages,
            sources=sources,
            page_content=page_content,
            active_page=active_page,
            is_owner=_is_owner(repo),
        )

    @repo_bp.route(
        "/<username>/<repo_slug>/settings",
        methods=["GET", "POST"],
        endpoint="settings",
    )
    @login_required
    def repo_settings(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)

        base = get_repo_path(Config.DATA_DIR, username, repo_slug)
        schema_path = os.path.join(base, "wiki", "schema.md")

        if request.method == "POST":
            action = request.form.get("action")
            if action == "update_info":
                repo.name = request.form.get("name", "").strip() or repo.name
                repo.description = request.form.get("description", "").strip()
                db.session.commit()
                flash("设置已保存", "success")
            elif action == "update_schema":
                content = request.form.get("schema_content", "")
                os.makedirs(os.path.dirname(schema_path), exist_ok=True)
                with open(schema_path, "w", encoding="utf-8") as f:
                    f.write(content)
                flash("Schema 已保存", "success")
            return redirect(
                url_for("repo.settings", username=username, repo_slug=repo_slug)
            )

        schema_content = ""
        if os.path.isfile(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_content = f.read()

        return render_template(
            "repo/settings.html",
            username=username,
            repo=repo,
            schema_content=schema_content,
        )

    @repo_bp.route("/<username>/<repo_slug>/delete", methods=["POST"])
    @login_required
    def delete(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)

        if app.qdrant:
            try:
                app.qdrant.delete_collection(repo.id)
            except Exception:
                logger.warning(
                    "Failed to delete Qdrant collection for repo %s", repo.id
                )

        Task.query.filter_by(repo_id=repo.id).delete()
        db.session.delete(repo)
        db.session.commit()

        repo_path = get_repo_path(Config.DATA_DIR, username, repo_slug)
        if os.path.isdir(repo_path):
            shutil.rmtree(repo_path, ignore_errors=True)

        flash("知识库已删除", "success")
        return redirect(url_for("repo.list_repos", username=username))

    app.register_blueprint(repo_bp)

    # ── Wiki ──────────────────────────────────────────────────────────

    wiki_bp = Blueprint("wiki", __name__)

    @wiki_bp.route("/<username>/<repo_slug>/wiki/<page_slug>")
    def view_page(username, repo_slug, page_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        wiki_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
        )
        filepath = os.path.join(wiki_dir, f"{page_slug}.md")
        if not os.path.isfile(filepath):
            abort(404)

        with open(filepath, "r", encoding="utf-8") as f:
            raw = f.read()

        fm, html = render_markdown(raw, _wiki_base_url(username, repo_slug))

        try:
            mtime = datetime.fromtimestamp(
                os.path.getmtime(filepath), tz=timezone.utc
            )
        except OSError:
            mtime = None

        page_sources = []
        if fm.get("source"):
            src_name = fm["source"]
            page_sources.append({"id": src_name, "filename": src_name})

        page = {
            "title": fm.get("title", page_slug),
            "type": fm.get("type", "unknown"),
            "type_label": TYPE_LABELS.get(fm.get("type", ""), fm.get("type", "")),
            "created_at": mtime,
            "updated_at": mtime,
            "sources": page_sources,
        }

        backlinks = get_backlinks(wiki_dir, page_slug)
        for bl in backlinks:
            bl["slug"] = bl["filename"].replace(".md", "")

        return render_template(
            "wiki/page.html",
            username=username,
            repo=repo,
            page=page,
            page_slug=page_slug,
            content=html,
            backlinks=backlinks,
            is_owner=_is_owner(repo),
        )

    @wiki_bp.route("/<username>/<repo_slug>/graph")
    def graph(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        wiki_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
        )
        pages = _enrich_pages(wiki_dir)
        page_slugs = {p["slug"] for p in pages}

        nodes = []
        links = []
        for p in pages:
            nodes.append(
                {
                    "id": p["slug"],
                    "title": p["title"],
                    "type": p.get("type", "unknown"),
                }
            )
            filepath = os.path.join(wiki_dir, p["filename"])
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            for target in extract_links(content):
                if target in page_slugs:
                    links.append({"source": p["slug"], "target": target})

        return render_template(
            "wiki/graph.html",
            username=username,
            repo=repo,
            graph_data={"nodes": nodes, "links": links},
        )

    @wiki_bp.route("/<username>/<repo_slug>/log")
    def log_page(username, repo_slug):
        _get_repo_or_404(username, repo_slug)
        return redirect(
            url_for(
                "wiki.view_page",
                username=username,
                repo_slug=repo_slug,
                page_slug="log",
            )
        )

    @wiki_bp.route("/<username>/<repo_slug>/wiki/<page_slug>/edit", methods=["GET", "POST"])
    @login_required
    def edit_page(username, repo_slug, page_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)
        wiki_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
        )
        filepath = os.path.join(wiki_dir, f"{page_slug}.md")
        if not os.path.isfile(filepath):
            abort(404)

        if request.method == "POST":
            content = request.form.get("content", "")
            if not content.strip():
                flash("内容不能为空", "error")
                return redirect(request.url)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            if current_app.qdrant:
                try:
                    fm, _ = render_markdown(content)
                    current_app.qdrant.upsert_page(
                        repo_id=repo.id,
                        filename=f"{page_slug}.md",
                        title=fm.get("title", page_slug),
                        page_type=fm.get("type", "unknown"),
                        content=content,
                    )
                except Exception:
                    logger.warning("Qdrant upsert failed for edited page %s", page_slug)
            _sync_repo_counts(repo, username)
            flash("页面已保存", "success")
            return redirect(
                url_for("wiki.view_page", username=username, repo_slug=repo_slug, page_slug=page_slug)
            )

        with open(filepath, "r", encoding="utf-8") as f:
            raw = f.read()
        return render_template(
            "wiki/edit.html",
            username=username,
            repo=repo,
            page_slug=page_slug,
            content=raw,
        )

    @wiki_bp.route("/<username>/<repo_slug>/wiki/<page_slug>/delete", methods=["POST"])
    @login_required
    def delete_page(username, repo_slug, page_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)
        wiki_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
        )
        filepath = os.path.join(wiki_dir, f"{page_slug}.md")
        if os.path.isfile(filepath):
            os.remove(filepath)
            if current_app.qdrant:
                try:
                    current_app.qdrant.delete_page(repo.id, f"{page_slug}.md")
                except Exception:
                    pass
            _sync_repo_counts(repo, username)
            flash(f"页面 {page_slug} 已删除", "success")
        else:
            flash("页面不存在", "error")
        return redirect(
            url_for("repo.dashboard", username=username, repo_slug=repo_slug)
        )

    app.register_blueprint(wiki_bp)

    # ── Source ────────────────────────────────────────────────────────

    source_bp = Blueprint("source", __name__)

    @source_bp.route("/<username>/<repo_slug>/sources")
    @login_required
    def list_sources(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        raw_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "raw"
        )
        return render_template(
            "source/list.html",
            username=username,
            repo=repo,
            sources=_enrich_sources(raw_dir, repo),
            is_owner=_is_owner(repo),
        )

    @source_bp.route("/<username>/<repo_slug>/sources/<source_id>")
    @login_required
    def view_source(username, repo_slug, source_id):
        user, repo = _get_repo_or_404(username, repo_slug)
        raw_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "raw"
        )
        filepath = os.path.join(raw_dir, source_id)
        if not os.path.isfile(filepath):
            abort(404)

        with open(filepath, "r", encoding="utf-8") as f:
            raw_content = f.read()
        _, html = render_markdown(raw_content)

        ingested = (
            Task.query.filter_by(
                repo_id=repo.id, type="ingest", status="done", input_data=source_id
            ).first()
            is not None
        )

        source = {"id": source_id, "filename": source_id, "ingested": ingested}
        return render_template(
            "source/view.html",
            username=username,
            repo=repo,
            source=source,
            content=html,
            is_owner=_is_owner(repo),
        )

    @source_bp.route("/<username>/<repo_slug>/sources/upload", methods=["POST"])
    @login_required
    def upload(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)

        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            flash("请选择要上传的文件", "error")
            return redirect(
                url_for("source.list_sources", username=username, repo_slug=repo_slug)
            )

        safe_name = secure_filename(uploaded.filename)
        if not safe_name or not _allowed_file(safe_name):
            flash("不支持的文件格式", "error")
            return redirect(
                url_for("source.list_sources", username=username, repo_slug=repo_slug)
            )

        base = get_repo_path(Config.DATA_DIR, username, repo_slug)
        raw_dir = os.path.join(base, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        ext = _file_ext(safe_name)

        saved_name = None

        if ext in ("md", "txt"):
            uploaded.save(os.path.join(raw_dir, safe_name))
            saved_name = safe_name
        elif ext in MINERU_EXTENSIONS:
            temp_dir = os.path.join(base, "temp")
            os.makedirs(temp_dir, exist_ok=True)
            temp_path = os.path.join(temp_dir, safe_name)
            uploaded.save(temp_path)

            # Convert .doc → .docx via LibreOffice before sending to MinerU
            parse_path = temp_path
            converted_path = None
            if ext == "doc":
                import subprocess
                try:
                    result_conv = subprocess.run(
                        ["soffice", "--headless", "--convert-to", "docx",
                         "--outdir", temp_dir, temp_path],
                        capture_output=True, timeout=60,
                    )
                    docx_path = os.path.splitext(temp_path)[0] + ".docx"
                    if result_conv.returncode == 0 and os.path.isfile(docx_path):
                        parse_path = docx_path
                        converted_path = docx_path
                    else:
                        logger.warning("LibreOffice conversion failed for %s: %s",
                                       safe_name, result_conv.stderr.decode())
                except Exception as conv_exc:
                    logger.warning("LibreOffice conversion error for %s: %s", safe_name, conv_exc)

            try:
                result = current_app.mineru.parse_file(parse_path)
                md_content = (
                    result.get("md_content")
                    or result.get("markdown")
                    or result.get("content", "")
                )
                if not md_content:
                    flash(f"MinerU 解析 {safe_name} 未返回内容", "error")
                else:
                    md_name = os.path.splitext(safe_name)[0] + ".md"
                    with open(os.path.join(raw_dir, md_name), "w", encoding="utf-8") as f:
                        f.write(md_content)
                    saved_name = md_name
            except MineruClientError as exc:
                logger.exception("MinerU parse failed for %s", safe_name)
                flash(f"文件解析失败: {exc}", "error")
                saved_name = None
            finally:
                if os.path.isfile(temp_path):
                    os.remove(temp_path)
                if converted_path and os.path.isfile(converted_path):
                    os.remove(converted_path)
                if os.path.isdir(temp_dir) and not os.listdir(temp_dir):
                    os.rmdir(temp_dir)
        else:
            flash("不支持的文件格式", "error")

        _sync_repo_counts(repo, username)

        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "XMLHttpRequest" in (request.headers.get("X-Requested-With", ""))
        if not is_xhr:
            is_xhr = "application/json" in request.accept_mimetypes

        if saved_name:
            task = Task(
                repo_id=repo.id,
                type="ingest",
                status="queued",
                input_data=saved_name,
            )
            db.session.add(task)
            db.session.commit()

            if is_xhr:
                return jsonify(ok=True, filename=saved_name, task_id=task.id)

            flash(
                f"文件 {saved_name} 上传成功，摄入任务已排队（#{task.id}）",
                "success",
            )
        elif is_xhr:
            return jsonify(ok=False, error="上传失败"), 400

        return redirect(
            url_for("source.list_sources", username=username, repo_slug=repo_slug)
        )

    @source_bp.route(
        "/<username>/<repo_slug>/sources/<source_id>/delete", methods=["POST"]
    )
    @login_required
    def delete_source(username, repo_slug, source_id):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)
        raw_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "raw"
        )
        filepath = os.path.join(raw_dir, source_id)
        if os.path.isfile(filepath):
            os.remove(filepath)
            removed = _purge_source_wiki(current_app._get_current_object(), repo, username, source_id)
            flash(f"已删除 {source_id}（清理了 {removed} 个关联 Wiki 页面）", "success")
        else:
            flash("文件不存在", "error")
        _sync_repo_counts(repo, username)
        return redirect(
            url_for("source.list_sources", username=username, repo_slug=repo_slug)
        )

    @source_bp.route(
        "/<username>/<repo_slug>/sources/<source_id>/rename", methods=["POST"]
    )
    @login_required
    def rename_source(username, repo_slug, source_id):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)
        new_name = request.form.get("new_name", "").strip()
        if not new_name:
            flash("文件名不能为空", "error")
            return redirect(
                url_for(
                    "source.list_sources", username=username, repo_slug=repo_slug
                )
            )
        safe_new = secure_filename(new_name)
        if not safe_new:
            flash("文件名包含非法字符", "error")
            return redirect(
                url_for(
                    "source.list_sources", username=username, repo_slug=repo_slug
                )
            )
        raw_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "raw"
        )
        old_path = os.path.join(raw_dir, source_id)
        new_path = os.path.join(raw_dir, safe_new)
        if not os.path.isfile(old_path):
            flash("源文件不存在", "error")
        elif os.path.exists(new_path):
            flash(f"{safe_new} 已存在", "error")
        else:
            os.rename(old_path, new_path)
            # Queue re-ingest so wiki reflects the renamed source
            task = Task(repo_id=repo.id, type="ingest", status="queued", input_data=safe_new)
            db.session.add(task)
            db.session.commit()
            flash(f"已重命名为 {safe_new}，已排队重新摄入（#{task.id}）", "success")
        return redirect(
            url_for("source.list_sources", username=username, repo_slug=repo_slug)
        )

    @source_bp.route(
        "/<username>/<repo_slug>/sources/batch-delete", methods=["POST"]
    )
    @login_required
    def batch_delete(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)
        filenames = request.form.getlist("files")
        raw_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "raw"
        )
        deleted = 0
        purged_wiki = 0
        app_obj = current_app._get_current_object()
        for fn in filenames:
            fp = os.path.join(raw_dir, secure_filename(fn))
            if os.path.isfile(fp):
                os.remove(fp)
                purged_wiki += _purge_source_wiki(app_obj, repo, username, secure_filename(fn))
                deleted += 1
        _sync_repo_counts(repo, username)
        flash(f"已删除 {deleted} 个文件，清理了 {purged_wiki} 个关联 Wiki 页面", "success")
        return redirect(
            url_for("source.list_sources", username=username, repo_slug=repo_slug)
        )

    @source_bp.route("/<username>/<repo_slug>/ingest/<source_id>", methods=["POST"])
    @login_required
    def ingest(username, repo_slug, source_id):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)

        raw_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "raw"
        )
        if not os.path.isfile(os.path.join(raw_dir, source_id)):
            flash("源文件不存在", "error")
            return redirect(
                url_for("source.list_sources", username=username, repo_slug=repo_slug)
            )

        task = Task(repo_id=repo.id, type="ingest", status="queued", input_data=source_id)
        db.session.add(task)
        db.session.commit()

        flash(f"摄入任务已排队（#{task.id}）", "success")
        return redirect(
            url_for("ops.task_queue", username=username, repo_slug=repo_slug)
        )

    app.register_blueprint(source_bp)

    # ── Ops ───────────────────────────────────────────────────────────

    ops_bp = Blueprint("ops", __name__)

    @ops_bp.route("/<username>/<repo_slug>/ingest-progress/<int:task_id>")
    @login_required
    def ingest_progress(username, repo_slug, task_id):
        user, repo = _get_repo_or_404(username, repo_slug)
        task = Task.query.get_or_404(task_id)
        if task.repo_id != repo.id:
            abort(404)
        return render_template(
            "ops/ingest_progress.html",
            username=username,
            repo=repo,
            task_id=task_id,
        )

    @ops_bp.route("/task/<int:task_id>/stream")
    @login_required
    def task_stream(task_id):
        """SSE endpoint that polls task progress from DB (worker runs in background)."""
        task = Task.query.get_or_404(task_id)
        repo = Repo.query.get_or_404(task.repo_id)
        if current_user.id != repo.user_id:
            abort(403)

        import time

        last_msg = ""

        def generate():
            nonlocal last_msg
            while True:
                db.session.expire_all()
                t = db.session.get(Task, task_id)
                if not t:
                    yield _sse("error_event", message="Task not found")
                    return

                msg = t.progress_msg or ""
                pct = t.progress or 0

                if msg != last_msg:
                    yield _sse("progress", percent=pct, message=msg)
                    yield _sse("step", message=msg, status="running")
                    last_msg = msg

                if t.status == "done":
                    yield _sse("progress", percent=100, message=msg)
                    yield _sse("done", message=msg)
                    return
                elif t.status == "failed":
                    yield _sse("error_event", message=msg or "Task failed")
                    return

                time.sleep(1)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @ops_bp.route("/<username>/<repo_slug>/tasks")
    @login_required
    def task_queue(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        tasks = (
            Task.query.filter_by(repo_id=repo.id)
            .order_by(Task.created_at.desc())
            .limit(50)
            .all()
        )
        return render_template(
            "ops/tasks.html",
            username=username,
            repo=repo,
            tasks=tasks,
            is_owner=_is_owner(repo),
        )

    @ops_bp.route("/api/tasks/<int:task_id>/status")
    @login_required
    def task_status_api(task_id):
        task = Task.query.get_or_404(task_id)
        repo = Repo.query.get_or_404(task.repo_id)
        if current_user.id != repo.user_id:
            abort(403)
        return jsonify(
            id=task.id,
            status=task.status,
            progress=task.progress,
            progress_msg=task.progress_msg,
            input_data=task.input_data,
        )

    @ops_bp.route(
        "/<username>/<repo_slug>/query", methods=["GET"], endpoint="query"
    )
    @login_required
    def query_page(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        return render_template("ops/query.html", username=username, repo=repo)

    @ops_bp.route(
        "/<username>/<repo_slug>/query", methods=["POST"], endpoint="query_api"
    )
    @login_required
    def query_api(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        data = request.get_json(silent=True) or {}
        question = data.get("q", "").strip()

        pre_answer = data.get("_rendered_answer")
        if pre_answer is not None:
            _, answer_html = render_markdown(pre_answer, _wiki_base_url(username, repo_slug))
            pre_wiki = data.get("_wiki_sources", [])
            pre_qdrant = data.get("_qdrant_sources", [])

            def _src_to_ref(fn):
                slug = fn.replace(".md", "")
                return {
                    "url": url_for("wiki.view_page", username=username,
                                   repo_slug=repo_slug, page_slug=slug),
                    "title": fn.replace(".md", "").replace("-", " ").title(),
                    "filename": fn,
                }

            return jsonify(
                html=answer_html,
                markdown=pre_answer,
                references=[_src_to_ref(fn) for fn in pre_wiki],
                wiki_sources=[_src_to_ref(fn) for fn in pre_wiki],
                qdrant_sources=[_src_to_ref(fn) for fn in pre_qdrant],
            )

        if not question:
            return jsonify(error="请输入问题"), 400

        try:
            result = current_app.wiki_engine.query(repo, username, question)
        except Exception as exc:
            logger.exception("Query failed for repo %s", repo.id)
            return jsonify(error=f"查询失败: {exc}"), 500

        _, answer_html = render_markdown(
            result.get("answer", ""), _wiki_base_url(username, repo_slug)
        )

        references = []
        for fn in result.get("referenced_pages", []):
            page_slug = fn.replace(".md", "")
            references.append(
                {
                    "url": url_for(
                        "wiki.view_page",
                        username=username,
                        repo_slug=repo_slug,
                        page_slug=page_slug,
                    ),
                    "title": fn.replace(".md", "").replace("-", " ").title(),
                }
            )

        def _fn_to_ref(fn: str) -> dict:
            slug = fn.replace(".md", "")
            return {
                "url": url_for("wiki.view_page", username=username,
                               repo_slug=repo_slug, page_slug=slug),
                "title": fn.replace(".md", "").replace("-", " ").title(),
                "filename": fn,
            }

        wiki_sources = [_fn_to_ref(fn) for fn in result.get("wiki_sources", [])]
        qdrant_sources = [_fn_to_ref(fn) for fn in result.get("qdrant_sources", [])]

        return jsonify(
            html=answer_html,
            markdown=result.get("answer", ""),
            references=references,
            wiki_sources=wiki_sources,
            qdrant_sources=qdrant_sources,
        )

    @ops_bp.route("/<username>/<repo_slug>/query/stream", methods=["GET"])
    @login_required
    def query_stream(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        question = request.args.get("q", "").strip()
        if not question:
            return jsonify(error="请输入问题"), 400

        def generate():
            try:
                for event_dict in current_app.wiki_engine.query_stream(repo, username, question):
                    event = event_dict["event"]
                    data = json.dumps(event_dict["data"], ensure_ascii=False)
                    yield f"event: {event}\ndata: {data}\n\n"
            except Exception as exc:
                logger.exception("query_stream error for repo %s", repo.id)
                yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @ops_bp.route("/<username>/<repo_slug>/query/save", methods=["POST"])
    @login_required
    def save_query_page(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)

        content = request.form.get("content", "")
        query_text = request.form.get("query", "").strip()
        if not content:
            flash("没有可保存的内容", "error")
            return redirect(
                url_for("ops.query", username=username, repo_slug=repo_slug)
            )

        slug = slugify(query_text) if query_text else "query-result"
        slug = slug or "query-result"
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if not content.startswith("---"):
            content = (
                f"---\ntitle: {query_text or 'Query Result'}\n"
                f"type: reference\nupdated: {now_str}\n---\n\n{content}"
            )

        wiki_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
        )
        os.makedirs(wiki_dir, exist_ok=True)

        filename = f"{slug}.md"
        counter = 1
        while os.path.exists(os.path.join(wiki_dir, filename)):
            filename = f"{slug}-{counter}.md"
            counter += 1

        with open(os.path.join(wiki_dir, filename), "w", encoding="utf-8") as f:
            f.write(content)

        if current_app.qdrant:
            try:
                fm, _ = render_markdown(content)
                current_app.qdrant.upsert_page(
                    repo_id=repo.id,
                    filename=filename,
                    title=fm.get("title", slug),
                    page_type=fm.get("type", "reference"),
                    content=content,
                )
            except Exception:
                logger.warning(
                    "Qdrant upsert failed for saved query page %s", filename
                )

        _sync_repo_counts(repo, username)
        flash(f"已保存为 Wiki 页面: {filename}", "success")
        return redirect(
            url_for(
                "wiki.view_page",
                username=username,
                repo_slug=repo_slug,
                page_slug=filename.replace(".md", ""),
            )
        )

    @ops_bp.route("/<username>/<repo_slug>/lint", methods=["GET", "POST"])
    @login_required
    def lint(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)

        try:
            raw_result = current_app.wiki_engine.lint(repo, username)
        except Exception as exc:
            logger.exception("Lint failed for repo %s", repo.id)
            flash(f"检查失败: {exc}", "error")
            return redirect(
                url_for("repo.dashboard", username=username, repo_slug=repo_slug)
            )

        issue_bucket_map = {
            "contradiction": "contradictions",
            "orphan": "orphan_pages",
            "missing_link": "missing_refs",
            "bad_frontmatter": "missing_refs",
            "wrong_type": "missing_refs",
        }
        report: dict[str, list] = {
            "contradictions": [],
            "stale_claims": [],
            "orphan_pages": [],
            "missing_pages": [],
            "missing_refs": [],
            "suggestions": [],
        }

        for issue in raw_result.get("issues", []):
            bucket = issue_bucket_map.get(issue.get("type", ""), "missing_refs")
            page_file = issue.get("page", "")
            report[bucket].append(
                {
                    "page_slug": page_file.replace(".md", "") if page_file else None,
                    "title": page_file.replace(".md", "") if page_file else None,
                    "message": issue.get("description", ""),
                }
            )

        for suggestion in raw_result.get("suggestions", []):
            report["suggestions"].append(
                {"page_slug": None, "title": None, "message": suggestion}
            )

        fixable_types = {"bad_frontmatter", "orphan", "missing_link", "wrong_type"}
        raw_issues = raw_result.get("issues", [])
        has_fixes = any(i.get("type") in fixable_types for i in raw_issues)

        return render_template(
            "ops/lint.html",
            username=username,
            repo=repo,
            report=report,
            has_fixes=has_fixes,
            raw_issues=raw_issues,
        )

    @ops_bp.route("/<username>/<repo_slug>/apply-fixes", methods=["POST"])
    @login_required
    def apply_fixes(username, repo_slug):
        user, repo = _get_repo_or_404(username, repo_slug)
        _require_owner(repo)

        issues_json = request.form.get("issues_json", "[]")
        try:
            issues = json.loads(issues_json)
        except json.JSONDecodeError:
            issues = []

        if not issues:
            flash("没有可修复的问题", "info")
            return redirect(url_for("ops.lint", username=username, repo_slug=repo_slug))

        try:
            result = current_app.wiki_engine.apply_fixes(repo, username, issues)
        except Exception as exc:
            logger.exception("apply_fixes failed for repo %s", repo.id)
            flash(f"修复失败: {exc}", "error")
            return redirect(url_for("ops.lint", username=username, repo_slug=repo_slug))

        fixed_count = len(result.get("fixed", []))
        skipped_count = len(result.get("skipped", []))
        flash(
            f"已修复 {fixed_count} 个问题，跳过 {skipped_count} 个（矛盾类问题需人工审查）",
            "success" if fixed_count > 0 else "info",
        )
        return redirect(url_for("ops.lint", username=username, repo_slug=repo_slug))

    app.register_blueprint(ops_bp)


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def _sse(event: str, **data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    application = create_app()
    with application.app_context():
        db.create_all()
    application.run(debug=True, host="0.0.0.0", port=5000)
