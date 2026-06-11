import pytest

from ecommerce_agent import cli


def test_serve_command_runs_app_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        calls["app"] = app
        calls.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main(["serve", "--host", "0.0.0.0", "--port", "9000", "--reload"])

    assert calls == {
        "app": "ecommerce_agent.api.app:create_app",
        "factory": True,
        "host": "0.0.0.0",
        "port": 9000,
        "reload": True,
    }


def test_default_command_serves_local_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        calls["app"] = app
        calls.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main([])

    assert calls["app"] == "ecommerce_agent.api.app:create_app"
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8000
    assert calls["reload"] is False
    assert calls["factory"] is True


def test_parser_has_eval_routing_subcommand() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["eval", "routing"])

    assert args.command == "eval"
    assert args.eval_target == "routing"
    assert callable(args.func)
