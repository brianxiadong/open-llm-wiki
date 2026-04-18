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


@cli.command("generate-token-key")
def generate_token_key():
    """生成一个 Fernet 密钥，放入 .env 的 API_TOKEN_ENC_KEY=<值>。"""
    from token_crypto import generate_key

    key = generate_key()
    click.echo(key)
    click.echo(
        "↑ 把这行写入 .env 的 API_TOKEN_ENC_KEY=<...>；配置后重启应用，"
        "用户就能在 /user/settings/tokens 列表里一键复制完整 token。",
        err=True,
    )


def _parse_migration_version(filename: str) -> int:
    """从 `004_foo.sql` 里提取 4；非法文件名抛 ValueError。"""
    return int(filename.split("_", 1)[0])


def list_migration_files(migrations_dir: str) -> list[str]:
    """按文件名字典序返回 migrations 目录下的 `.sql` 文件。"""
    if not os.path.isdir(migrations_dir):
        return []
    return sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql"))


def plan_migrations(
    *,
    on_disk: list[str],
    applied_filenames: set[str],
    legacy_applied_versions: set[int],
) -> tuple[list[str], list[str]]:
    """计算迁移计划（纯函数，方便单测）。

    返回 ``(to_backfill, to_run)``：
    - ``to_backfill``：磁盘上存在、filename 集合里没有、但 version 号已在
      ``legacy_applied_versions``（老 schema 只记 version）的文件；
      这些被认作『历史已跑』，只写记录不执行 SQL。
    - ``to_run``：磁盘上存在、且既不在 applied_filenames、也不在 legacy
      version 集合里的文件；真正需要执行的新迁移。
    """
    to_backfill: list[str] = []
    to_run: list[str] = []
    for filename in on_disk:
        if filename in applied_filenames:
            continue
        try:
            version = _parse_migration_version(filename)
        except ValueError:
            continue
        if version in legacy_applied_versions:
            to_backfill.append(filename)
        else:
            to_run.append(filename)
    return to_backfill, to_run


_SCHEMA_VERSION_MODERN_DDL = """
    CREATE TABLE schema_version (
        id INT AUTO_INCREMENT PRIMARY KEY,
        version INT NOT NULL,
        filename VARCHAR(255) NULL,
        applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_schema_version_filename (filename),
        INDEX idx_schema_version_version (version)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _detect_schema_shape(cursor) -> str:
    """返回 'absent' / 'legacy' / 'modern'。

    legacy = 表已存在但没有 filename 列（老版本 `version INT PRIMARY KEY`）。
    modern = 表已有 filename 列（本次重构后的结构）。
    """
    cursor.execute("SHOW TABLES LIKE 'schema_version'")
    if cursor.fetchone() is None:
        return "absent"
    cursor.execute("SHOW COLUMNS FROM schema_version LIKE 'filename'")
    return "modern" if cursor.fetchone() is not None else "legacy"


@cli.command()
def migrate():
    """执行数据库迁移（filename 为唯一键，兼容老的 version-only schema）。"""
    import pymysql

    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = int(os.environ.get("DB_PORT", "3306"))
    user = os.environ.get("DB_USER", "root")
    password = os.environ.get("DB_PASSWORD", "")
    database = os.environ.get("DB_NAME", "llmwiki")

    conn = pymysql.connect(
        host=host, port=port, user=user, password=password,
        database=database, charset="utf8mb4",
    )
    cursor = conn.cursor()

    shape = _detect_schema_shape(cursor)

    if shape == "absent":
        cursor.execute(_SCHEMA_VERSION_MODERN_DDL)
        conn.commit()
    elif shape == "legacy":
        # 老结构：version 是 PK，无法保存多个同 version 记录。
        # 通过 create_new + copy + rename 一次性升级到新结构（保留 applied_at）。
        cursor.execute(
            "SELECT version, applied_at FROM schema_version ORDER BY version"
        )
        legacy_rows = cursor.fetchall()
        cursor.execute("DROP TABLE IF EXISTS schema_version_new")
        cursor.execute(_SCHEMA_VERSION_MODERN_DDL.replace(
            "CREATE TABLE schema_version",
            "CREATE TABLE schema_version_new",
        ))
        for v, at in legacy_rows:
            cursor.execute(
                "INSERT INTO schema_version_new (version, filename, applied_at) "
                "VALUES (%s, NULL, %s)",
                (v, at),
            )
        cursor.execute("DROP TABLE schema_version")
        cursor.execute("RENAME TABLE schema_version_new TO schema_version")
        conn.commit()
        click.echo(
            f"schema_version 已从 version-only 结构升级为 filename 唯一键结构"
            f"（保留 {len(legacy_rows)} 条历史记录作为 legacy-NULL 行）"
        )

    # 现在 schema 一定是 modern，读取当前状态
    cursor.execute("SELECT filename FROM schema_version WHERE filename IS NOT NULL")
    applied_filenames = {r[0] for r in cursor.fetchall()}
    cursor.execute("SELECT DISTINCT version FROM schema_version WHERE filename IS NULL")
    legacy_versions = {r[0] for r in cursor.fetchall()}

    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
    on_disk = list_migration_files(migrations_dir)
    if not on_disk:
        click.echo("无迁移文件。")
        cursor.close()
        conn.close()
        return

    to_backfill, to_run = plan_migrations(
        on_disk=on_disk,
        applied_filenames=applied_filenames,
        legacy_applied_versions=legacy_versions,
    )

    # 回填：同 version 的所有磁盘文件都标记为『历史已应用』，不执行 SQL
    #      （生产要么真的跑过、要么业务表已含目标字段——避免重复 ALTER 报错）
    for filename in to_backfill:
        version = _parse_migration_version(filename)
        cursor.execute(
            "INSERT IGNORE INTO schema_version (version, filename) VALUES (%s, %s)",
            (version, filename),
        )
        click.echo(f"回填历史迁移记录: {filename}")
    if to_backfill:
        # 清理 filename IS NULL 的占位行：它们已被具体 filename 行替代
        cursor.execute("DELETE FROM schema_version WHERE filename IS NULL")
        conn.commit()

    applied = 0
    for filename in to_run:
        version = _parse_migration_version(filename)
        filepath = os.path.join(migrations_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            sql = f.read()
        try:
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
            cursor.execute(
                "INSERT INTO schema_version (version, filename) VALUES (%s, %s)",
                (version, filename),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            click.echo(f"迁移失败，已回滚: {filename}", err=True)
            raise
        click.echo(f"已执行迁移: {filename}")
        applied += 1

    if applied == 0 and not to_backfill:
        click.echo("数据库已是最新。")
    cursor.close()
    conn.close()


@cli.command("show-migrations")
def show_migrations():
    """列出每条迁移脚本的应用状态（✓ 已执行 / ⨯ 待执行 / ? 文件缺失但已应用）。"""
    import pymysql

    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = int(os.environ.get("DB_PORT", "3306"))
    user = os.environ.get("DB_USER", "root")
    password = os.environ.get("DB_PASSWORD", "")
    database = os.environ.get("DB_NAME", "llmwiki")

    conn = pymysql.connect(
        host=host, port=port, user=user, password=password,
        database=database, charset="utf8mb4",
    )
    cursor = conn.cursor()

    cursor.execute("SHOW TABLES LIKE 'schema_version'")
    if cursor.fetchone() is None:
        click.echo("(schema_version 表不存在，请先运行 `manage.py migrate`)")
        cursor.close()
        conn.close()
        return

    cursor.execute("SHOW COLUMNS FROM schema_version LIKE 'filename'")
    has_filename = cursor.fetchone() is not None
    if has_filename:
        cursor.execute(
            "SELECT filename, version, applied_at FROM schema_version ORDER BY filename"
        )
        rows = cursor.fetchall()
        applied_filenames = {r[0] for r in rows if r[0] is not None}
        legacy_rows = [r for r in rows if r[0] is None]
    else:
        cursor.execute("SELECT version, applied_at FROM schema_version ORDER BY version")
        rows = [(None, r[0], r[1]) for r in cursor.fetchall()]
        applied_filenames = set()
        legacy_rows = rows

    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
    on_disk = list_migration_files(migrations_dir)
    on_disk_set = set(on_disk)

    click.echo(f"磁盘迁移文件数: {len(on_disk)}")
    for f in on_disk:
        status = "✓" if f in applied_filenames else "⨯"
        click.echo(f"  {status} {f}")

    if legacy_rows:
        click.echo("\n历史 version-only 记录（未绑定到具体 filename）：")
        for _, version, at in legacy_rows:
            click.echo(f"  version={version} applied_at={at}")

    missing = applied_filenames - on_disk_set
    if missing:
        click.echo("\n已应用但磁盘上不存在的文件（可能被重命名/删除）：")
        for f in sorted(missing):
            click.echo(f"  ? {f}")

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

    mail_required = ("MAIL_HOST", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_FROM")
    missing_mail = [key for key in mail_required if not os.environ.get(key)]
    if missing_mail:
        results["mail"] = f"fail: missing {', '.join(missing_mail)}"
    else:
        results["mail"] = "ok"

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


@cli.command("rebuild-fact-index")
@click.option("--repo-id", default=None, type=int, help="只重建指定 repo 的 fact 索引")
@click.option("--regenerate", is_flag=True, help="从 raw/originals/ 重新生成 JSONL 再索引")
def rebuild_fact_index(repo_id, regenerate):
    """重建 Qdrant fact 索引（用于表格 records 回填）"""
    from app import create_app
    from models import Repo
    from utils import (
        build_tabular_markdown_and_records,
        get_repo_path,
        read_jsonl,
        write_jsonl,
    )

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
            facts_dir = os.path.join(base, "facts", "records")
            os.makedirs(facts_dir, exist_ok=True)

            if regenerate:
                _regenerate_jsonl_from_originals(base, facts_dir)

            fact_files = sorted(f for f in os.listdir(facts_dir) if f.endswith(".jsonl"))
            click.echo(f"Rebuilding facts for repo={repo.id} ({repo.slug}): {len(fact_files)} files")
            for fact_file in fact_files:
                source_filename = os.path.splitext(fact_file)[0] + ".md"
                try:
                    records = read_jsonl(os.path.join(facts_dir, fact_file))
                    app.qdrant.upsert_fact_records(
                        repo_id=repo.id,
                        source_filename=source_filename,
                        records=records,
                    )
                    click.echo(f"  OK: {fact_file} ({len(records)} records)")
                except Exception as exc:
                    click.echo(f"  FAIL: {fact_file}: {exc}")
    click.echo("Done.")


def _regenerate_jsonl_from_originals(base: str, facts_dir: str):
    """Re-parse original Excel/CSV files to regenerate JSONL with current logic."""
    import csv
    import io

    from utils import build_tabular_markdown_and_records, write_jsonl

    originals_dir = os.path.join(base, "raw", "originals")
    if not os.path.isdir(originals_dir):
        return
    for fname in sorted(os.listdir(originals_dir)):
        lower = fname.lower()
        stem = os.path.splitext(fname)[0]
        fpath = os.path.join(originals_dir, fname)
        tables: list[dict] = []

        if lower.endswith((".xlsx", ".xls")):
            import openpyxl

            wb = openpyxl.load_workbook(fpath, data_only=True, read_only=True)
            for ws in wb.worksheets:
                rows = [list(row) for row in ws.iter_rows(values_only=True)]
                if rows:
                    tables.append({"name": ws.title, "rows": rows})
            wb.close()
        elif lower.endswith(".csv"):
            with open(fpath, "r", encoding="utf-8-sig") as cf:
                reader = csv.reader(cf)
                rows = [row for row in reader]
            if rows:
                tables.append({"name": stem, "rows": rows})
        else:
            continue

        if not tables:
            continue

        md_filename = f"{stem}.md"
        markdown, records = build_tabular_markdown_and_records(
            source_filename=fname,
            tables=tables,
            source_markdown_filename=md_filename,
        )
        raw_md_path = os.path.join(base, "raw", md_filename)
        with open(raw_md_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        jsonl_path = os.path.join(facts_dir, f"{stem}.jsonl")
        write_jsonl(jsonl_path, records)
        click.echo(f"  Regenerated: {fname} → {len(records)} records")


@cli.command("search-query-logs")
@click.option("--keyword", "-k", default=None, help="在问题或回答中搜索关键词")
@click.option("--date", "-d", "log_date", default=None, help="指定日期 YYYY-MM-DD，默认今天")
@click.option("--confidence", "-c", default=None, type=click.Choice(["high", "medium", "low"]), help="按置信度筛选")
@click.option("--mode", "-m", default=None, type=click.Choice(["fact", "narrative", "hybrid"]), help="按查询模式筛选")
@click.option("--tail", "-n", default=20, show_default=True, help="最多显示最新 N 条")
@click.option("--full", is_flag=True, help="显示完整回答（默认只显示前 200 字）")
def search_query_logs(keyword, log_date, confidence, mode, tail, full):
    """在每日 JSONL 日志中搜索查询记录，方便追溯用户反馈。

    示例：
      python manage.py search-query-logs -k "LLaMA 2" -c low
      python manage.py search-query-logs -d 2026-04-13 --full
      python manage.py search-query-logs -k "训练数据" -n 5 --full
    """
    import glob
    import json
    from config import Config

    log_dir = os.path.join(Config.DATA_DIR, "logs")
    if not os.path.isdir(log_dir):
        click.echo(f"日志目录不存在: {log_dir}", err=True)
        return

    if log_date:
        patterns = [os.path.join(log_dir, f"query_trace_{log_date}.jsonl")]
    else:
        patterns = sorted(glob.glob(os.path.join(log_dir, "query_trace_*.jsonl")))

    records = []
    for path in patterns:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if confidence and rec.get("confidence", {}).get("level") != confidence:
                    continue
                if mode and rec.get("mode") != mode:
                    continue
                if keyword:
                    kw = keyword.lower()
                    if kw not in (rec.get("question") or "").lower() and \
                       kw not in (rec.get("answer") or "").lower():
                        continue
                records.append(rec)

    records = records[-tail:]
    if not records:
        click.echo("没有找到匹配的记录。")
        return

    click.echo(f"找到 {len(records)} 条记录（最新 {tail} 条）\n")
    sep = "─" * 72

    for i, r in enumerate(records, 1):
        conf = r.get("confidence", {})
        conf_level = conf.get("level", "?")
        conf_score = conf.get("score", 0)
        wiki_n = len(r.get("wiki_hits", []))
        chunk_n = len(r.get("chunk_hits", []))
        fact_n = len(r.get("fact_hits", []))
        latency = r.get("latency_ms")
        latency_str = f"{latency}ms" if latency is not None else "?"

        click.echo(sep)
        click.echo(f"[{i}] {r.get('ts', '?')}  repo={r.get('repo', '?')}  user={r.get('user', '?')}")
        click.echo(f"    mode={r.get('mode','?')}  confidence={conf_level}({conf_score:.2f})  latency={latency_str}")
        click.echo(f"    证据: wiki×{wiki_n}  chunk×{chunk_n}  fact×{fact_n}")
        click.echo(f"  Q: {r.get('question', '')}")

        if wiki_n:
            for h in r["wiki_hits"]:
                click.echo(f"    [Wiki] {h.get('filename','')} — {h.get('reason','')}")
        if chunk_n:
            for h in r["chunk_hits"]:
                score_pct = f"{int((h.get('score') or 0)*100)}%"
                snippet = (h.get("snippet") or "")[:80].replace("\n", " ")
                click.echo(f"    [Chunk {score_pct}] {h.get('filename','')} | {snippet}")
        if fact_n:
            for h in r["fact_hits"]:
                score_pct = f"{int((h.get('score') or 0)*100)}%"
                fields_str = ", ".join(f"{k}={v}" for k, v in list((h.get("fields") or {}).items())[:3])
                click.echo(f"    [Fact {score_pct}] {h.get('source_file','')} | {fields_str}")

        answer = r.get("answer", "")
        if not full:
            answer = answer[:200] + ("…" if len(answer) > 200 else "")
        click.echo(f"  A: {answer}")

    click.echo(sep)
    click.echo(f"\n共 {len(records)} 条。使用 --full 查看完整回答，-k 按关键词筛选。")


if __name__ == "__main__":
    cli()
