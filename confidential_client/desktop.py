"""Desktop entrypoint for the confidential client."""

from __future__ import annotations

from confidential_client.gui import launch_desktop_app
from confidential_client.version import CLIENT_NAME, CLIENT_VERSION


def main() -> None:
    print(f"{CLIENT_NAME} {CLIENT_VERSION}")
    launch_desktop_app()


if __name__ == "__main__":
    main()
