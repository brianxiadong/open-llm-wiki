"""Tests for SQLAlchemy models."""

import pytest
from sqlalchemy.exc import IntegrityError

from models import db, User, Repo, Task, load_user


def test_user_create(app):
    with app.app_context():
        u = User(username="bob", display_name="Bob")
        u.set_password("secret")
        db.session.add(u)
        db.session.commit()
        u2 = User.query.filter_by(username="bob").first()
        assert u2 is not None
        assert u2.username == "bob"
        assert u2.display_name == "Bob"
        assert u2.password_hash
        assert u2.id is not None


def test_user_password(app):
    with app.app_context():
        u = User(username="carol")
        u.set_password("correct-horse")
        db.session.add(u)
        db.session.commit()
        uid = u.id
        u = db.session.get(User, uid)
        assert u.check_password("correct-horse") is True
        assert u.check_password("wrong") is False


def test_user_unique_username(app):
    with app.app_context():
        u1 = User(username="dup")
        u1.set_password("a")
        db.session.add(u1)
        db.session.commit()
        u2 = User(username="dup")
        u2.set_password("b")
        db.session.add(u2)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_repo_create(app):
    with app.app_context():
        u = User(username="owner")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        r = Repo(
            user_id=u.id,
            name="My Repo",
            slug="my-repo",
            description="desc",
        )
        db.session.add(r)
        db.session.commit()
        r2 = Repo.query.filter_by(slug="my-repo").first()
        assert r2.name == "My Repo"
        assert r2.slug == "my-repo"
        assert r2.description == "desc"
        assert r2.user_id == u.id
        assert r2.user.username == "owner"


def test_repo_unique_constraint(app):
    with app.app_context():
        u = User(username="u1")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        db.session.add(Repo(user_id=u.id, name="A", slug="same"))
        db.session.commit()
        db.session.add(Repo(user_id=u.id, name="B", slug="same"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_task_create(app):
    with app.app_context():
        u = User(username="taskuser")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        r = Repo(user_id=u.id, name="R", slug="r")
        db.session.add(r)
        db.session.commit()
        t = Task(repo_id=r.id, type="ingest", status="pending", input_data='{"k":1}')
        db.session.add(t)
        db.session.commit()
        t2 = Task.query.filter_by(repo_id=r.id).first()
        assert t2.type == "ingest"
        assert t2.status == "pending"
        assert t2.input_data == '{"k":1}'
        assert t2.repo.slug == "r"


def test_user_loader(app):
    with app.app_context():
        u = User(username="loader_user")
        u.set_password("pw")
        db.session.add(u)
        db.session.commit()
        uid = str(u.id)
        loaded = load_user(uid)
        assert loaded is not None
        assert loaded.id == u.id
        assert loaded.username == "loader_user"
        assert load_user("999999") is None


def test_repo_is_public_default_false(sample_repo, app):
    client, repo_info = sample_repo
    with app.app_context():
        from models import Repo
        repo = Repo.query.filter_by(slug=repo_info["slug"]).first()
        assert repo.is_public == False


def test_set_repo_public(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/settings",
        data={"action": "update_info", "name": "Test KB", "description": "", "is_public": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        from models import Repo
        repo = Repo.query.filter_by(slug=slug).first()
        assert repo.is_public == True
