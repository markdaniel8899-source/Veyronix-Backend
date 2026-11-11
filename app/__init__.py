"""
Veyronix Backend - Flask application factory.
"""

import hmac
import os
import warnings

warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, jsonify, request
from flask_cors import CORS

from .config import Config
from .utils.logger import get_logger, setup_logger


def _bearer_token_from_request() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("X-API-Token", "").strip()


def create_app(config_class=Config):
    """Flask application factory."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False

    logger = setup_logger('Veyronix')

    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process

    # Required directories
    for directory in (
        app.config.get('UPLOAD_FOLDER'),
        app.config.get('OASIS_SIMULATION_DATA_DIR'),
        os.path.dirname(app.config.get('LOG_FILE') or ''),
    ):
        if directory:
            os.makedirs(directory, exist_ok=True)

    if should_log_startup:
        logger.info("=" * 50)
        logger.info("Veyronix Backend starting...")
        logger.info("Database: %s", app.config.get('DATABASE_PATH'))
        logger.info("Log file: %s", app.config.get('LOG_FILE'))
        logger.info("=" * 50)

    # CORS — comma-separated origins in production, "*" by default.
    raw_origins = str(app.config.get('CORS_ORIGINS') or '*')
    origins = [item.strip() for item in raw_origins.split(',') if item.strip()]
    CORS(
        app,
        resources={r"/api/*": {"origins": origins or ["*"]}},
        allow_headers=["Content-Type", "Authorization", "X-API-Token"],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    )

    # Bearer-token authentication for every API route.
    @app.before_request
    def authenticate_api():
        if request.method == "OPTIONS":
            return None
        if not request.path.startswith("/api/"):
            return None

        expected = str(app.config.get('API_BEARER_TOKEN') or '')
        if not expected:
            logger.error("API_BEARER_TOKEN is not configured; rejecting API request")
            return jsonify(
                {"success": False, "error": "Server authentication is not configured"}
            ), 503

        provided = _bearer_token_from_request()
        if not provided or not hmac.compare_digest(provided, expected):
            return jsonify(
                {"success": False, "error": "Unauthorized"}
            ), 401
        return None

    @app.before_request
    def log_request():
        logger = get_logger('Veyronix.request')
        logger.debug("Request: %s %s", request.method, request.path)

    @app.after_request
    def log_response(response):
        logger = get_logger('Veyronix.request')
        logger.debug("Response: %s", response.status_code)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.errorhandler(413)
    def payload_too_large(_error):
        return jsonify({
            "success": False,
            "error": "Uploaded file is too large",
        }), 413

    @app.errorhandler(500)
    def internal_error(error):
        logger.exception("Unhandled server error: %s", error)
        return jsonify({
            "success": False,
            "error": "Internal server error",
        }), 500

    # Register atexit cleanup for simulation workers.
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("Simulation cleanup hook registered")

    # Blueprints
    from .api import graph_bp, report_bp, simulation_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')

    @app.route('/health')
    def health():
        return {
            'status': 'ok',
            'service': 'Veyronix Backend',
            'database': 'sqlite',
        }

    if should_log_startup:
        logger.info("Veyronix Backend startup complete")

    return app
