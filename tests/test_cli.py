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


def test_parser_accepts_eval_tool_choice() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["eval", "tool-choice"])

    assert args.command == "eval"
    assert args.eval_target == "tool-choice"
    assert callable(args.func)


def test_parser_still_accepts_eval_routing() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["eval", "routing"])

    assert args.eval_target == "routing"


def test_users_add_parser() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(
        ["users", "add", "--username", "alice", "--role", "operator", "--spring-user-id", "7"]
    )

    assert args.command == "users"
    assert args.users_command == "add"
    assert args.username == "alice"
    assert args.role == "operator"
    assert args.spring_user_id == 7


def test_sessions_backfill_owner_parser() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["sessions", "backfill-owner", "--owner-id", "seed-operator"])

    assert args.command == "sessions"
    assert args.sessions_command == "backfill-owner"
    assert args.owner_id == "seed-operator"


def test_users_add_creates_user(monkeypatch: pytest.MonkeyPatch) -> None:
    created = {}

    class Store:
        @classmethod
        def from_settings(cls, settings):  # noqa: ANN001
            return cls()

        async def ensure_indexes(self) -> None:
            pass

        async def create(self, user) -> None:  # noqa: ANN001
            created["user"] = user

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "MongoUserStore", Store)
    monkeypatch.setattr(cli, "_prompt_password", lambda: "pw")

    parser = cli.build_parser()
    args = parser.parse_args(
        ["users", "add", "--username", "alice", "--role", "operator", "--spring-user-id", "7"]
    )
    args.func(args)

    assert created["user"].username == "alice"
    assert created["user"].role == "operator"
    assert created["user"].spring_user_id == 7
    assert created["user"].password_hash.startswith("$argon2")


def test_sessions_backfill_owner_updates_ownerless_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {}

    class Store:
        @classmethod
        def from_settings(cls, settings):  # noqa: ANN001
            return cls()

        async def backfill_ownerless(self, *, owner_id: str) -> int:
            called["owner_id"] = owner_id
            return 3

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "MongoSessionStore", Store)

    parser = cli.build_parser()
    args = parser.parse_args(["sessions", "backfill-owner", "--owner-id", "seed-operator"])
    args.func(args)

    assert called == {"owner_id": "seed-operator"}


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


def test_eval_tool_choice_dispatch_runs_branch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import ecommerce_agent.config as config_module
    import ecommerce_agent.evals.tool_choice as tc
    from ecommerce_agent.evals.tool_choice import ToolChoiceReport

    monkeypatch.setattr(config_module, "get_settings", lambda: object())
    monkeypatch.setattr(tc, "load_tool_choice_cases", lambda: ["case"])
    monkeypatch.setattr(tc, "build_stub_sales_analyst", lambda settings: "ANALYST")

    async def fake_run(agent, cases, **kwargs):
        assert agent == "ANALYST"
        assert cases == ["case"]
        return ToolChoiceReport(
            n=1,
            passed=1,
            accuracy=1.0,
            per_tag_accuracy={"aggregate": 1.0},
            per_expected_tool_accuracy={"get_statistics": 1.0},
            aggregate_authority_miss_rate=0.0,
            post_choice_errors=0,
            errors_before_choice=0,
            cases=[],
        )

    monkeypatch.setattr(tc, "run_tool_choice_eval", fake_run)

    cli.run_eval_command(argparse.Namespace(eval_target="tool-choice"))

    out = capsys.readouterr().out
    assert "tool-choice accuracy=1.00" in out
    assert "aggregate_authority_miss_rate=0.00" in out
