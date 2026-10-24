#!/usr/bin/env python
"""Run Django management commands with the same configuration as the API."""

import os
import sys


def main() -> None:
    """Run Django management commands using command-line arguments.

    Uses runtime settings unless DJANGO_SETTINGS_MODULE is already set.

    Returns
    -------
    None
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "second_brain.django_config.settings.runtime")

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
