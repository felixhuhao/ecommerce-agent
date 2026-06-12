import argparse

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


def test_parser_accepts_eval_approval_safety() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["eval", "approval-safety"])

    assert args.command == "eval"
    assert args.eval_target == "approval-safety"
    assert callable(args.func)


def test_parser_still_accepts_eval_routing() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["eval", "routing"])

    assert args.eval_target == "routing"


def test_eval_approval_safety_dispatch_runs_branch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import ecommerce_agent.config as config_module
    import ecommerce_agent.evals.approval_safety as aps
    from ecommerce_agent.evals.approval_safety import ApprovalReport

    monkeypatch.setattr(config_module, "get_settings", lambda: object())
    monkeypatch.setattr(aps, "load_approval_cases", lambda: ["case"])
    monkeypatch.setattr(aps, "build_stub_order_manager", lambda settings, calls: "AGENT")

    async def fake_run(agent, cases, **kwargs):
        assert agent == "AGENT"
        assert cases == ["case"]
        return ApprovalReport(
            n=1,
            passed=1,
            errors=0,
            accuracy=1.0,
            per_tag_accuracy={},
            false_proposal_rate=0.0,
            missed_proposal_rate=0.0,
            confusion={"proposed": {"proposed": 1}},
            cases=[],
        )

    monkeypatch.setattr(aps, "run_approval_safety_eval", fake_run)

    cli.run_eval_command(argparse.Namespace(eval_target="approval-safety"))

    out = capsys.readouterr().out
    assert "approval-safety accuracy=1.00" in out
    assert "false_proposal_rate=0.00" in out
