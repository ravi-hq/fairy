#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    # Add src/ to the Python path so Django can find the packages
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
