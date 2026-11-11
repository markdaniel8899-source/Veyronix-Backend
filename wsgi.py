"""Production WSGI entry point for gunicorn.

Usage:
    gunicorn --worker-class gthread --workers 1 --threads 4 \
        --bind 127.0.0.1:5001 --timeout 300 wsgi:app
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config

errors = Config.validate()
if errors:
    raise RuntimeError(
        "Invalid Veyronix configuration: " + "; ".join(errors)
    )

app = create_app()
