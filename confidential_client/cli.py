"""CLI for confidential local knowledge bases."""

from __future__ import annotations

import json
from pathlib import Path

import click

from confidential_client.repository import ConfidentialRepository
from confidential_client.runtime import ConfidentialRuntime
from llmwiki_core.contracts import ConfidentialServices


@click.group()
def cli() -> None:
    """Confidential KB local client."""


@cli.command("create")
@click.argument("repo_dir")
@click.option("--name", required=True)
@click.option("--slug", required=True)
@click.option("--passphrase", required=True)
@click.option("--services-file", type=click.Path(exists=True, dir_okay=False), required=True)
def create_repo(repo_dir: str, name: str, slug: str, passphrase: str, services_file: str) -> None:
    services = ConfidentialServices.from_dict(
        json.loads(Path(services_file).read_text(encoding="utf-8"))
    )
    repo = ConfidentialRepository.create(
        repo_dir,
        name=name,
        slug=slug,
        passphrase=passphrase,
        services=services,
    )
    click.echo(f"created confidential repo: {repo.manifest.slug} ({repo.repo_dir})")


@cli.command("ingest")
@click.argument("repo_dir")
@click.argument("source_path")
@click.option("--passphrase", required=True)
def ingest(repo_dir: str, source_path: str, passphrase: str) -> None:
    runtime = ConfidentialRuntime(ConfidentialRepository(repo_dir), passphrase)
    events = runtime.ingest_file(source_path)
    click.echo(json.dumps(events, ensure_ascii=False, indent=2))


@cli.command("query")
@click.argument("repo_dir")
@click.argument("question")
@click.option("--passphrase", required=True)
def query(repo_dir: str, question: str, passphrase: str) -> None:
    runtime = ConfidentialRuntime(ConfidentialRepository(repo_dir), passphrase)
    result = runtime.query(question)
    click.echo(result.answer)
    click.echo(json.dumps(result.confidence, ensure_ascii=False))


@cli.command("history")
@click.argument("repo_dir")
@click.option("--passphrase", required=True)
def history(repo_dir: str, passphrase: str) -> None:
    runtime = ConfidentialRuntime(ConfidentialRepository(repo_dir), passphrase)
    click.echo(json.dumps(runtime.load_history(), ensure_ascii=False, indent=2))


@cli.command("export")
@click.argument("repo_dir")
@click.argument("output_path")
def export_bundle(repo_dir: str, output_path: str) -> None:
    repo = ConfidentialRepository(repo_dir)
    bundle = repo.export_bundle(output_path)
    click.echo(str(bundle))


if __name__ == "__main__":
    cli()
