"""Publish the control plane's runtime configuration as a Modal Secret.

The allow list below is the point of this file. A deployment needs a handful of
values from the developer's `.env`, and the failure everyone reaches for is
copying the file wholesale — which would put the test database URL, local paths
and anything else that ever lands there into a platform secret. Naming the keys
makes that impossible by accident and reviewable on sight.

The direction matters as much as the filter: the values are ours and live in a
file we keep, and the platform is given a copy. Typing them into a provider's
dashboard would make that provider the place the truth lives, and this project
treats a deployment target as a configuration axis — Modal runs inference today
and something else may run it tomorrow.

Values are never printed, never written into a shell string and never recorded.
The command is built as an argument list, so nothing passes through a shell, and
the only output is key names.

    .venv\\Scripts\\python.exe tools/sync_control_secret.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SECRET_NAME = "assistant-control"

# What the deployed control plane reads. Local paths are excluded because a
# Windows path means nothing in a Linux container, and `AGENT_TEST_DATABASE_URL`
# is excluded because a deployment must never be able to reach the database the
# test suite creates and drops schemas in.
#
# A pair means the value is read from the first name and published under the
# second. That exists for one specific problem: a value the container must have
# and the local profile must not. `WEB_RENDERER_URL` is exactly that — it is
# what decides where a page is opened, so a copy sitting in `.env` under its own
# name would silently send every local `view_web_page` to the deployed renderer,
# starting a container to do what the browser on this machine does for free.
ALLOWED: tuple[str | tuple[str, str], ...] = (
    "TELEGRAM_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "TELEGRAM_ALLOWED_USERS",
    "AGENT_DATABASE_URL",
    "AGENT_DATABASE_SCHEMA",
    # Only for measuring one database against another; see the latency report.
    "AGENT_ALT_DATABASE_URL",
    "MODEL_ENDPOINT",
    "MODEL_NAME",
    "MODEL_API_KEY",
    "MODEL_AUTH_STYLE",
    "MODEL_CHAT_TEMPLATE_KWARGS",
    "MODEL_EXTRA_BODY",
    # The web capability. The search key and the renderer's proxy token are
    # credentials; the identity is a courtesy to sites that ask for one.
    "WEB_FIRECRAWL_API_KEY",
    "WEB_RENDERER_KEY",
    "WEB_FALLBACK_USER_AGENT",
    ("DEPLOY_WEB_RENDERER_URL", "WEB_RENDERER_URL"),
)


def named(entry: str | tuple[str, str]) -> tuple[str, str]:
    """(name in our file, name in the deployed environment)."""

    return (entry, entry) if isinstance(entry, str) else entry


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def plan(values: dict[str, str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split the allow list into what will be published and what is absent.

    Separate from `main` so the decision about what leaves this machine can be
    tested without running anything.
    """

    pairs = [named(entry) for entry in ALLOWED]
    present = [(source, target) for source, target in pairs if values.get(source)]
    missing = [(source, target) for source, target in pairs if not values.get(source)]
    return present, missing


def describe(source: str, target: str) -> str:
    return source if source == target else f"{source} -> {target}"


def main() -> int:
    source = Path(".env")
    if not source.is_file():
        print("no .env in the working directory")
        return 1

    values = read_env(source)
    present, missing = plan(values)

    print(f"publishing {len(present)} keys to the {SECRET_NAME} secret:")
    for names in present:
        print(f"  + {describe(*names)}")
    for names in missing:
        print(f"  - {describe(*names)} (absent from .env, not published)")

    if not present:
        print("nothing to publish")
        return 1

    # `--force` replaces the secret rather than adding to it, which is correct
    # only because this list is the whole intended contents: a key that stops
    # being published stops existing, instead of lingering in the platform after
    # it was removed here.
    command = [
        sys.executable,
        "-m",
        "modal",
        "secret",
        "create",
        SECRET_NAME,
        *(f"{target}={values[source]}" for source, target in present),
        "--force",
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
