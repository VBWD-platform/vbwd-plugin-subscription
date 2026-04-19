#!/usr/bin/env python
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
"""Run subscription populate_db inside the running Flask app context."""
from vbwd.app import create_app

app = create_app()
with app.app_context():
    from plugins.subscription.populate_db import populate
    populate(app)
    print("Subscription populate complete.")
