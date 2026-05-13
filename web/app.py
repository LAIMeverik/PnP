"""Flask application entry point."""
import os
from flask import Flask, send_from_directory

from web.db import init_db
from web.api.warehouse import bp as warehouse_bp
from web.api.bom import bp as bom_bp
from web.api.pnp import bp as pnp_bp


def create_app():
    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(__file__), 'static'),
        template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
    )
    app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB

    # Init DB on first startup
    init_db()

    # Register blueprints
    app.register_blueprint(warehouse_bp)
    app.register_blueprint(bom_bp)
    app.register_blueprint(pnp_bp)

    @app.get('/')
    def index():
        return send_from_directory(app.template_folder, 'index.html')

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    return app


if __name__ == '__main__':
    application = create_app()
    application.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('FLASK_DEBUG', '0') == '1',
    )
