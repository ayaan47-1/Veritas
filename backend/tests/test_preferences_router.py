from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from backend.app.auth import deps as auth_deps
from backend.app.database import get_db
from backend.app.main import create_app
from backend.app.models import User
from backend.app.models.enums import OIDCProvider, UserRole
from backend.app.services.unsubscribe_token import mint_unsubscribe_token


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def first(self):
        for row in self._rows():
            if self._matches_all(row):
                return row
        return None

    def all(self):
        return [row for row in self._rows() if self._matches_all(row)]

    def _rows(self):
        if self._model is User:
            return list(self._session.users)
        return []

    def _matches_all(self, row):
        return all(self._matches(row, c) for c in self._conditions)

    def _matches(self, row, expr):
        left = getattr(expr, "left", None)
        right = getattr(expr, "right", None)
        key = getattr(left, "key", None)
        if key is None or right is None:
            return True
        right_value = getattr(right, "value", right)
        return getattr(row, key, None) == right_value


class FakeSession:
    def __init__(self, users: list[User] | None = None):
        self.users = users or []
        self.committed = 0

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, _obj):
        return None

    def commit(self):
        self.committed += 1

    def rollback(self):
        return None

    def close(self):
        return None


def _make_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="u@example.com",
        name="Test",
        oidc_provider=OIDCProvider.clerk,
        oidc_subject="sub-1",
        role=UserRole.reviewer,
        is_active=True,
        digest_enabled=True,
        digest_timezone="America/Chicago",
    )


def _client(db: FakeSession, current_user: User) -> TestClient:
    app = create_app()

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[auth_deps.get_current_user] = lambda: current_user
    app.dependency_overrides[auth_deps.require_authenticated] = lambda: current_user
    return TestClient(app)


def test_get_my_preferences():
    user = _make_user()
    db = FakeSession(users=[user])
    client = _client(db, user)
    resp = client.get("/users/me/preferences")
    assert resp.status_code == 200
    assert resp.json() == {
        "digest_enabled": True,
        "digest_timezone": "America/Chicago",
    }


def test_put_my_preferences_toggles_enabled():
    user = _make_user()
    db = FakeSession(users=[user])
    client = _client(db, user)
    resp = client.put("/users/me/preferences", json={"digest_enabled": False})
    assert resp.status_code == 200
    assert resp.json()["digest_enabled"] is False
    assert user.digest_enabled is False


def test_put_my_preferences_rejects_invalid_timezone():
    user = _make_user()
    db = FakeSession(users=[user])
    client = _client(db, user)
    resp = client.put(
        "/users/me/preferences", json={"digest_timezone": "Not/AZone"}
    )
    assert resp.status_code == 400


def test_put_my_preferences_updates_timezone():
    user = _make_user()
    db = FakeSession(users=[user])
    client = _client(db, user)
    resp = client.put(
        "/users/me/preferences", json={"digest_timezone": "America/New_York"}
    )
    assert resp.status_code == 200
    assert user.digest_timezone == "America/New_York"


def test_put_my_preferences_empty_body_rejected():
    user = _make_user()
    db = FakeSession(users=[user])
    client = _client(db, user)
    resp = client.put("/users/me/preferences", json={})
    assert resp.status_code == 400


def test_unsubscribe_with_valid_token_flips_flag(monkeypatch):
    monkeypatch.setenv("DIGEST_UNSUBSCRIBE_SECRET", "test-secret")
    user = _make_user()
    assert user.digest_enabled is True
    db = FakeSession(users=[user])
    client = _client(db, user)

    token = mint_unsubscribe_token(user.id, "test-secret")
    resp = client.post(f"/users/unsubscribe/{token}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "email": user.email}
    assert user.digest_enabled is False


def test_unsubscribe_idempotent(monkeypatch):
    monkeypatch.setenv("DIGEST_UNSUBSCRIBE_SECRET", "test-secret")
    user = _make_user()
    user.digest_enabled = False
    db = FakeSession(users=[user])
    client = _client(db, user)

    token = mint_unsubscribe_token(user.id, "test-secret")
    resp = client.post(f"/users/unsubscribe/{token}")
    assert resp.status_code == 200
    # No extra commit on already-false.
    assert db.committed == 0


def test_unsubscribe_invalid_token_404(monkeypatch):
    monkeypatch.setenv("DIGEST_UNSUBSCRIBE_SECRET", "test-secret")
    db = FakeSession(users=[])
    user = _make_user()
    client = _client(db, user)
    resp = client.post("/users/unsubscribe/not-a-real-token")
    assert resp.status_code == 404


def test_unsubscribe_missing_secret_503(monkeypatch):
    monkeypatch.delenv("DIGEST_UNSUBSCRIBE_SECRET", raising=False)
    user = _make_user()
    db = FakeSession(users=[user])
    client = _client(db, user)
    token = mint_unsubscribe_token(user.id, "test-secret")
    resp = client.post(f"/users/unsubscribe/{token}")
    assert resp.status_code == 503
