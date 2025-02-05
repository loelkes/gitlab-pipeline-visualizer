from flask import Flask
from .routes import routes


def create_app():
    app = Flask(__name__)
    app.register_blueprint(routes)
    return app


def cli():
    app = create_app()
    app.run(debug=True)
