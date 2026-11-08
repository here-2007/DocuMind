import argparse
import time

from documind.config import settings
from documind.db import engine
from documind.repository import Repository
from documind.storage import store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "dispatch", "collect-orphans"])
    args = parser.parse_args()
    repo = Repository(engine(), store(), settings())
    if args.command == "init":
        repo.initialize()
        print("Initialized local workspace and private object store.")
    elif args.command == "collect-orphans":
        repo.collect_orphans()
    else:
        from documind.worker import dispatch_once

        last_collection = 0.0
        while True:
            try:
                dispatch_once()
                if time.monotonic() - last_collection > 300:
                    repo.collect_orphans()
                    last_collection = time.monotonic()
            except Exception:
                print('{"event":"dispatcher_dependency_unavailable"}', flush=True)
            time.sleep(settings().dispatch_poll_seconds)


if __name__ == "__main__":
    main()
