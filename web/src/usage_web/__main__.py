import argparse
import sys
from pathlib import Path

from .config import Settings


def _serve() -> None:
    import uvicorn

    from .app import create_app

    settings = Settings()
    if not settings.USAGE_READER_DSN:
        sys.exit("USAGE_READER_DSN is required for serve")
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.PORT)  # noqa: S104


def _migrate() -> None:
    from .migrate import apply

    settings = Settings()
    if not settings.USAGE_OWNER_DSN:
        sys.exit("USAGE_OWNER_DSN is required for migrate")
    applied = apply(settings.USAGE_OWNER_DSN, Path(settings.MIGRATIONS_DIR))
    print(f"applied migrations: {', '.join(applied) or 'none'}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="usage-web")
    parser.add_argument("command", choices=["serve", "migrate"])
    args = parser.parse_args()
    {"serve": _serve, "migrate": _migrate}[args.command]()


if __name__ == "__main__":
    main()
