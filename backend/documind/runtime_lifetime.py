"""Run one model process with a hard lifetime, including dependency/model startup."""

import argparse
import os
import signal
import subprocess
from datetime import UTC, datetime


def bounded(command, expires_at):
    remaining = (expires_at - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        return 124
    child = subprocess.Popen(command, start_new_session=True)

    def terminate(*args):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    previous = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        try:
            return child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            terminate()
            child.wait()
            return 124
    finally:
        terminate()
        child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.expires_at)
    if deadline.tzinfo is None or not args.command:
        parser.error("An aware expiry and command are required")
    raise SystemExit(bounded(args.command, deadline))


if __name__ == "__main__":
    main()
