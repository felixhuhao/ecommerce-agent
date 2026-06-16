import argparse
import getpass
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import uvicorn

from ecommerce_agent.auth.users_store import MongoUserStore
from ecommerce_agent.sessions.store import MongoSessionStore

APP_FACTORY = "ecommerce_agent.api.app:create_app"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecommerce-agent")
    parser.set_defaults(func=serve, host="127.0.0.1", port=8000, reload=False)

    subparsers = parser.add_subparsers(dest="command")
    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI app")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.set_defaults(func=serve)

    eval_parser = subparsers.add_parser("eval", help="Run an eval")
    eval_parser.add_argument(
        "eval_target",
        choices=["routing", "approval-safety", "tool-choice", "groundedness"],
    )
    eval_parser.set_defaults(func=run_eval_command)

    users_parser = subparsers.add_parser("users", help="Manage users")
    users_subparsers = users_parser.add_subparsers(dest="users_command", required=True)
    add_user_parser = users_subparsers.add_parser("add", help="Create a user")
    add_user_parser.add_argument("--username", required=True)
    add_user_parser.add_argument("--role", required=True, choices=["viewer", "operator"])
    add_user_parser.add_argument("--spring-user-id", dest="spring_user_id", type=int, required=True)
    add_user_parser.set_defaults(func=run_users_command)

    sessions_parser = subparsers.add_parser("sessions", help="Manage conversation sessions")
    sessions_subparsers = sessions_parser.add_subparsers(
        dest="sessions_command",
        required=True,
    )
    backfill_parser = sessions_subparsers.add_parser(
        "backfill-owner",
        help="Assign an owner to legacy sessions with no owner_id",
    )
    backfill_parser.add_argument("--owner-id", required=True)
    backfill_parser.set_defaults(func=run_sessions_command)

    return parser


def serve(args: argparse.Namespace) -> None:
    uvicorn.run(
        APP_FACTORY,
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def run_eval_command(args: argparse.Namespace) -> None:
    if args.eval_target == "tool-choice":
        _run_tool_choice_cli()
        return
    if args.eval_target == "approval-safety":
        _run_approval_safety_cli()
        return
    if args.eval_target == "groundedness":
        _run_groundedness_cli()
        return
    if args.eval_target != "routing":
        raise ValueError(f"unsupported eval target: {args.eval_target}")

    import asyncio

    from ecommerce_agent.config import get_settings
    from ecommerce_agent.evals.routing import compare, load_routing_cases, run_routing_eval
    from ecommerce_agent.models import get_classifier_model
    from ecommerce_agent.routing.keyword import KeywordRouter
    from ecommerce_agent.routing.registry import build_specialist_registry
    from ecommerce_agent.routing.router import ClassifierRouter

    settings = get_settings()
    cases = load_routing_cases()
    registry = build_specialist_registry()

    async def _run():
        keyword = await run_routing_eval(
            KeywordRouter(registry),
            cases,
            router_name="keyword",
        )
        classifier = await run_routing_eval(
            ClassifierRouter(get_classifier_model(settings), registry),
            cases,
            router_name="classifier",
        )
        return keyword, classifier

    keyword, classifier = asyncio.run(_run())
    delta = compare(keyword, classifier)
    keyword_adversarial = keyword.per_tag_accuracy.get("adversarial")
    classifier_adversarial = classifier.per_tag_accuracy.get("adversarial")
    print(f"keyword    accuracy={keyword.accuracy:.2f} adversarial={keyword_adversarial}")
    print(f"classifier accuracy={classifier.accuracy:.2f} adversarial={classifier_adversarial}")
    print(
        f"delta overall={delta['overall_delta']:+.2f} "
        f"adversarial={delta['adversarial_delta']:+.2f} flips={delta['flips']}"
    )


def _run_approval_safety_cli() -> None:
    import asyncio

    from ecommerce_agent.config import get_settings
    from ecommerce_agent.evals.approval_safety import (
        load_approval_cases,
        run_approval_safety_eval_by_specialist,
    )

    settings = get_settings()
    cases = load_approval_cases()
    report = asyncio.run(run_approval_safety_eval_by_specialist(settings, cases))
    print(
        f"approval-safety accuracy={report.accuracy:.2f} "
        f"false_proposal_rate={report.false_proposal_rate:.2f} "
        f"missed_proposal_rate={report.missed_proposal_rate:.2f}"
    )
    print(f"confusion={report.confusion}")


def _run_tool_choice_cli() -> None:
    import asyncio

    from ecommerce_agent.config import get_settings
    from ecommerce_agent.evals.tool_choice import (
        build_stub_sales_analyst,
        load_tool_choice_cases,
        run_tool_choice_eval,
    )

    settings = get_settings()
    cases = load_tool_choice_cases()
    agent = build_stub_sales_analyst(settings)
    report = asyncio.run(run_tool_choice_eval(agent, cases))
    print(
        f"tool-choice accuracy={report.accuracy:.2f} "
        f"aggregate_authority_miss_rate={report.aggregate_authority_miss_rate:.2f}"
    )
    print(f"per_tag_accuracy={report.per_tag_accuracy}")


def _run_groundedness_cli() -> None:
    import asyncio

    from ecommerce_agent.config import get_settings
    from ecommerce_agent.evals.groundedness import run_groundedness_eval

    report = asyncio.run(run_groundedness_eval(get_settings()))
    print(
        f"groundedness n={report.n} "
        f"unsupported_claim_rate={report.unsupported_claim_rate:.3f} "
        f"partial_rate={report.partial_rate:.3f}"
    )
    print(f"per_authority={report.per_authority}")


def _prompt_password() -> str:
    return getpass.getpass("Password: ")


def run_users_command(args: argparse.Namespace) -> None:
    import asyncio

    from ecommerce_agent.auth.models import Role, User
    from ecommerce_agent.auth.passwords import hash_password
    from ecommerce_agent.config import get_settings

    password = _prompt_password()
    user = User(
        user_id=uuid.uuid4().hex,
        username=args.username,
        password_hash=hash_password(password),
        role=Role(args.role),
        spring_user_id=args.spring_user_id,
        created_at=datetime.now(UTC).isoformat(),
    )
    store = MongoUserStore.from_settings(get_settings())

    async def _run() -> None:
        await store.ensure_indexes()
        await store.create(user)

    try:
        asyncio.run(_run())
    finally:
        store.close()
    print(f"created user {user.username} ({user.role})")


def run_sessions_command(args: argparse.Namespace) -> None:
    import asyncio

    from ecommerce_agent.config import get_settings

    if args.sessions_command != "backfill-owner":
        raise ValueError(f"unsupported sessions command: {args.sessions_command}")

    store = MongoSessionStore.from_settings(get_settings())

    async def _run() -> int:
        return await store.backfill_ownerless(owner_id=args.owner_id)

    try:
        count = asyncio.run(_run())
    finally:
        store.close()
    print(f"backfilled {count} legacy sessions to owner {args.owner_id}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
