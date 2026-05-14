#!/usr/bin/env python
"""Run subscription populate_db inside the running Flask app context."""
import os
import sys


def main() -> None:
    sys.path.insert(
        0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    )
    from vbwd.app import create_app
    from plugins.subscription.populate_db import populate

    app = create_app()
    with app.app_context():
        populate(app)
        print("Subscription populate complete.")


if __name__ == "__main__":
    main()
