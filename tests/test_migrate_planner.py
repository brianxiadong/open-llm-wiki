"""manage.plan_migrations 纯函数单测。"""

from __future__ import annotations

import pytest

from manage import _parse_migration_version, plan_migrations


def test_parse_version_ok():
    assert _parse_migration_version("004_foo.sql") == 4
    assert _parse_migration_version("010_api_tokens_scopes_expiry.sql") == 10


def test_parse_version_rejects_malformed():
    with pytest.raises(ValueError):
        _parse_migration_version("foo.sql")


def test_plan_skips_already_applied_by_filename():
    on_disk = ["001_init.sql", "002_task_queue.sql"]
    backfill, to_run = plan_migrations(
        on_disk=on_disk,
        applied_filenames={"001_init.sql", "002_task_queue.sql"},
        legacy_applied_versions=set(),
    )
    assert backfill == []
    assert to_run == []


def test_plan_runs_only_unknown_files():
    on_disk = [
        "001_init.sql",
        "009_repo_sharing.sql",
        "010_api_tokens_scopes_expiry.sql",
    ]
    backfill, to_run = plan_migrations(
        on_disk=on_disk,
        applied_filenames={"001_init.sql", "009_repo_sharing.sql"},
        legacy_applied_versions=set(),
    )
    assert backfill == []
    assert to_run == ["010_api_tokens_scopes_expiry.sql"]


def test_plan_backfills_legacy_versions_without_running_sql():
    """老库只记 version，磁盘上同 version 多个文件应全部回填、不重复执行 SQL。"""
    on_disk = [
        "004_query_log_full_trace.sql",
        "004_query_logs.sql",
        "005_query_log_trace_id.sql",
        "005_wave3.sql",
        "010_api_tokens_scopes_expiry.sql",
    ]
    backfill, to_run = plan_migrations(
        on_disk=on_disk,
        applied_filenames=set(),
        legacy_applied_versions={4, 5},
    )
    assert set(backfill) == {
        "004_query_log_full_trace.sql",
        "004_query_logs.sql",
        "005_query_log_trace_id.sql",
        "005_wave3.sql",
    }
    assert to_run == ["010_api_tokens_scopes_expiry.sql"]


def test_plan_legacy_version_not_blocking_higher_version_new_file():
    """关键 regression：legacy version=4 不应再阻挡 version=10 的新文件执行。"""
    on_disk = [
        "004_old_collision_a.sql",
        "004_old_collision_b.sql",
        "010_new_migration.sql",
    ]
    backfill, to_run = plan_migrations(
        on_disk=on_disk,
        applied_filenames=set(),
        legacy_applied_versions={4, 5, 6, 7, 8, 9},
    )
    assert set(backfill) == {"004_old_collision_a.sql", "004_old_collision_b.sql"}
    assert to_run == ["010_new_migration.sql"]


def test_plan_mixed_filename_and_legacy_state():
    """迁移到新 schema 之后再跑：applied_filenames 有内容，legacy 为空。"""
    on_disk = [
        "001_init.sql",
        "010_api_tokens.sql",
        "011_new_thing.sql",
    ]
    backfill, to_run = plan_migrations(
        on_disk=on_disk,
        applied_filenames={"001_init.sql", "010_api_tokens.sql"},
        legacy_applied_versions=set(),
    )
    assert backfill == []
    assert to_run == ["011_new_thing.sql"]


def test_plan_ignores_malformed_filenames():
    """非法命名的文件（缺下划线、首段非数字）应被跳过，不报错。"""
    on_disk = ["README.sql", "001_init.sql"]
    backfill, to_run = plan_migrations(
        on_disk=on_disk,
        applied_filenames=set(),
        legacy_applied_versions=set(),
    )
    # README.sql 被忽略；001_init.sql 进 to_run
    assert backfill == []
    assert to_run == ["001_init.sql"]
