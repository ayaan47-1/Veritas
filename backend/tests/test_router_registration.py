from __future__ import annotations

import sys
import types


if "celery" not in sys.modules:
    celery_module = types.ModuleType("celery")

    class _DummyCelery:
        def __init__(self, *args, **kwargs):
            self.conf = {}

        def autodiscover_tasks(self, *args, **kwargs) -> None:
            return None

        def task(self, *args, **kwargs):
            def _decorator(func):
                return func

            return _decorator

    celery_module.Celery = _DummyCelery
    sys.modules["celery"] = celery_module


from backend.app.main import create_app


def _route_methods(app):
    route_map = {}
    for route in app.routes:
        methods = getattr(route, "methods", set())
        if not methods:
            continue
        route_map.setdefault(route.path, set()).update(methods)
    return route_map


def test_all_mvp_routers_are_registered():
    app = create_app()
    routes = _route_methods(app)

    expected = [
        ("/obligations", "GET"),
        ("/obligations/{obligation_id}", "GET"),
        ("/obligations/{obligation_id}/review", "POST"),
        ("/risks", "GET"),
        ("/risks/{risk_id}", "GET"),
        ("/risks/{risk_id}/review", "POST"),
        ("/entities", "GET"),
        ("/entities/suggestions", "GET"),
        ("/entities/{entity_id}/merge", "POST"),
        ("/entity-mentions/{mention_id}/resolve", "POST"),
        ("/summary/weekly", "GET"),
        ("/summary/weekly/narrative", "GET"),
        ("/assets", "GET"),
        ("/assets", "POST"),
        ("/assets/{asset_id}", "GET"),
        ("/auth/login/{provider}", "GET"),
        ("/auth/callback", "GET"),
        ("/users/me", "GET"),
        ("/users", "GET"),
        ("/users/{user_id}/role", "PUT"),
        ("/users/{user_id}/assets", "POST"),
        ("/users/{user_id}/assets/{asset_id}", "DELETE"),
        ("/notifications", "GET"),
        ("/notifications/{notification_id}/read", "PUT"),
        ("/config", "GET"),
        ("/config/{key}", "PUT"),
        ("/config/{key}", "DELETE"),
    ]

    for path, method in expected:
        assert path in routes, f"missing route path: {path}"
        assert method in routes[path], f"missing method {method} for {path}"
