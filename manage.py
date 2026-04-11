import os
import sys
import click
import httpx
from dotenv import load_dotenv

load_dotenv()


@click.group()
def cli():
    """Open LLM Wiki 管理工具"""
    pass


@cli.command()
def init_db():
    """初始化数据库（通过 SQLAlchemy 建表）"""
    from app import create_app
    from models import db

    app = create_app()
    with app.app_context():
        db.create_all()
    click.echo("数据库初始化完成。")


@cli.command()
def migrate():
    """执行数据库迁移"""
    import pymysql

    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = int(os.environ.get("DB_PORT", "3306"))
    user = os.environ.get("DB_USER", "root")
    password = os.environ.get("DB_PASSWORD", "")
    database = os.environ.get("DB_NAME", "llmwiki")

    conn = pymysql.connect(host=host, port=port, user=user, password=password, database=database, charset="utf8mb4")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INT PRIMARY KEY,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    conn.commit()

    cursor.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
    current_version = cursor.fetchone()[0]

    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
    if not os.path.isdir(migrations_dir):
        click.echo("无迁移文件。")
        cursor.close()
        conn.close()
        return

    migration_files = sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql"))
    applied = 0
    for filename in migration_files:
        version = int(filename.split("_")[0])
        if version <= current_version:
            continue
        filepath = os.path.join(migrations_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            sql = f.read()
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                cursor.execute(statement)
        cursor.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))
        conn.commit()
        click.echo(f"已执行迁移: {filename}")
        applied += 1

    if applied == 0:
        click.echo("数据库已是最新。")
    cursor.close()
    conn.close()


@cli.command()
@click.argument("username")
@click.argument("password")
@click.option("--display-name", default=None)
def create_user(username, password, display_name):
    """通过命令行创建用户"""
    from app import create_app
    from models import db, User

    app = create_app()
    with app.app_context():
        if User.query.filter_by(username=username).first():
            click.echo(f"用户 {username} 已存在。")
            sys.exit(1)
        user = User(username=username, display_name=display_name or username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"用户 {username} 创建成功。")


@cli.command()
def check():
    """检查外部服务连通性"""
    results = {}

    # MySQL
    try:
        import pymysql

        conn = pymysql.connect(
            host=os.environ.get("DB_HOST"),
            port=int(os.environ.get("DB_PORT", 3306)),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD"),
            database=os.environ.get("DB_NAME"),
        )
        conn.close()
        results["mysql"] = "ok"
    except Exception as e:
        results["mysql"] = f"fail: {e}"

    # MinerU
    try:
        url = os.environ.get("MINERU_API_URL", "")
        resp = httpx.get(f"{url}/health", timeout=5)
        results["mineru"] = "ok" if resp.status_code == 200 else f"fail: HTTP {resp.status_code}"
    except Exception as e:
        results["mineru"] = f"fail: {e}"

    # Qdrant
    try:
        url = os.environ.get("QDRANT_URL", "")
        resp = httpx.get(f"{url}/healthz", timeout=5)
        results["qdrant"] = "ok" if resp.status_code == 200 else f"fail: HTTP {resp.status_code}"
    except Exception as e:
        results["qdrant"] = f"fail: {e}"

    # Embedding
    try:
        from openai import OpenAI

        base = os.environ.get("EMBEDDING_API_BASE", "")
        key = os.environ.get("EMBEDDING_API_KEY") or "dummy"
        client = OpenAI(base_url=base, api_key=key)
        client.embeddings.create(model=os.environ.get("EMBEDDING_MODEL", "bge-m3"), input="test")
        results["embedding"] = "ok"
    except Exception as e:
        results["embedding"] = f"fail: {e}"

    for service, status in results.items():
        icon = "✓" if status == "ok" else "✗"
        click.echo(f"  {icon} {service}: {status}")


@cli.command("rebuild-chunk-index")
@click.option("--repo-id", default=None, type=int, help="只重建指定 repo 的 chunk 索引")
def rebuild_chunk_index(repo_id):
    """重建 Qdrant chunk 索引（用于存量数据回填）"""
    from app import create_app
    from models import Repo
    from utils import get_repo_path, list_wiki_pages

    app = create_app()
    with app.app_context():
        if not app.qdrant:
            click.echo("Qdrant 不可用，跳过。")
            return
        query = Repo.query
        if repo_id:
            query = query.filter_by(id=repo_id)
        repos = query.all()
        for repo in repos:
            user = repo.user
            base = get_repo_path(app.config["DATA_DIR"], user.username, repo.slug)
            wiki_dir = os.path.join(base, "wiki")
            if not os.path.isdir(wiki_dir):
                continue
            pages = list_wiki_pages(wiki_dir)
            click.echo(f"Rebuilding chunks for repo={repo.id} ({repo.slug}): {len(pages)} pages")
            for page in pages:
                fpath = os.path.join(wiki_dir, page["filename"])
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                    app.qdrant.upsert_page_chunks(
                        repo_id=repo.id,
                        filename=page["filename"],
                        title=page["title"],
                        page_type=page["type"],
                        content=content,
                    )
                    click.echo(f"  OK: {page['filename']}")
                except Exception as exc:
                    click.echo(f"  FAIL: {page['filename']}: {exc}")
    click.echo("Done.")


if __name__ == "__main__":
    cli()
