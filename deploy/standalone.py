"""Run the API and Caddy together in the single-image distribution."""

import signal
import subprocess
import sys
import time

children = []
stopping = False


def request_stop(_signal, _frame):
    global stopping
    stopping = True


def main():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        children.append(subprocess.Popen(
            [sys.executable, "-B", "-m", "backend.public_server"],
            cwd="/app/pipeline_ui",
        ))
        children.append(subprocess.Popen(
            ["/usr/bin/caddy", "run", "--config", "/etc/caddy/Caddyfile",
             "--adapter", "caddyfile"],
        ))
        while not stopping:
            for child in children:
                result = child.poll()
                if result is not None:
                    print(f"NormGraph service {child.pid} exited with {result}", flush=True)
                    return result if result != 0 else 1
            time.sleep(0.2)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
