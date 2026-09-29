#!/usr/bin/env python
"""Django management entrypoint for the Quanifi report browser under web/."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web.config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        raise ImportError(
            "Django could not be imported. Install the web dependencies with "
            "`pip install -r web/requirements.txt`, or activate the project "
            "virtual environment first."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
