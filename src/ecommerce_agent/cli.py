import argparse
from collections.abc import Sequence

import uvicorn

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
    eval_parser.add_argument("eval_target", choices=["routing"])
    eval_parser.set_defaults(func=run_eval_command)

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
    import asyncio

    from ecommerce_agent.config import get_settings
    from ecommerce_agent.evals.routing import compare, load_routing_cases, run_routing_eval
    from ecommerce_agent.models import get_classifier_model
    from ecommerce_agent.routing.keyword import KeywordRouter
    from ecommerce_agent.routing.registry import build_specialist_registry
    from ecommerce_agent.routing.router import ClassifierRouter

    if args.eval_target != "routing":
        raise ValueError(f"unsupported eval target: {args.eval_target}")

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
    print(
        f"classifier accuracy={classifier.accuracy:.2f} "
        f"adversarial={classifier_adversarial}"
    )
    print(
        f"delta overall={delta['overall_delta']:+.2f} "
        f"adversarial={delta['adversarial_delta']:+.2f} flips={delta['flips']}"
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
