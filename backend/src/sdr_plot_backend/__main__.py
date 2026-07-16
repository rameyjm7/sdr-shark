import faulthandler
import os
import socket
import subprocess
import threading
from pathlib import Path
from flask import Flask, abort, send_from_directory
from flask_cors import CORS
from sdr_plot_backend.actions import actions_blueprint
from sdr_plot_backend.api import api_blueprint
from sdr_plot_backend.file_manager import file_mgr_blueprint
from sdr_plot_backend.sigid_plugin import sigid_plugin_blueprint

faulthandler.enable()

def create_app():
    app = Flask(__name__, static_folder=None)
    app.register_blueprint(api_blueprint)
    app.register_blueprint(actions_blueprint)
    app.register_blueprint(file_mgr_blueprint)
    app.register_blueprint(sigid_plugin_blueprint)
    CORS(app)
    register_frontend_routes(app)
    # Start the frontend in a separate thread
    threading.Thread(target=start_frontend, daemon=True).start()
    return app

def frontend_build_dir():
    return Path(__file__).resolve().parents[3] / "frontend" / "build"

def register_frontend_routes(app):
    build_dir = frontend_build_dir()

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_frontend(path):
        if path.startswith(("api/", "actions/", "files/", "sigid/")):
            abort(404)
        index_path = build_dir / "index.html"
        if not index_path.exists():
            abort(404)
        if path:
            requested = build_dir / path
            if requested.is_file():
                return send_from_directory(build_dir, path)
        return send_from_directory(build_dir, "index.html")

def start_frontend():
    """Launch the development frontend only when no production build is served."""
    auto_start = os.getenv("SDR_SHARK_AUTO_START_FRONTEND", "1").strip().lower()
    if auto_start in {"0", "false", "no"}:
        print("Frontend auto-start disabled by SDR_SHARK_AUTO_START_FRONTEND.")
        return

    mode = os.getenv("SDR_SHARK_FRONTEND_MODE", "auto").strip().lower()
    build_dir = frontend_build_dir()
    if (build_dir / "index.html").exists() and mode != "dev":
        print(f"Serving production frontend from {build_dir}; skipping npm start.")
        return
    if mode in {"production", "prod", "static"}:
        print(f"Production frontend requested but {build_dir / 'index.html'} was not found.")
        return

    frontend_dir = Path(__file__).resolve().parents[3] / "frontend"
    if not frontend_dir.exists():
        print(f"Frontend directory not found at {frontend_dir}; skipping frontend auto-start.")
        return

    react_scripts_bin = frontend_dir / "node_modules" / ".bin" / "react-scripts"
    if not react_scripts_bin.exists():
        print(
            f"Frontend dependencies missing in {frontend_dir}. "
            "Run 'npm install' (or 'yarn install') in frontend/."
        )
        return
    frontend_port = int(os.getenv("SDR_SHARK_FRONTEND_PORT", "3000"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(("127.0.0.1", frontend_port)) == 0:
            print(f"Frontend already appears to be running on port {frontend_port}; skipping auto-start.")
            return
    try:
        env = os.environ.copy()
        env.setdefault("BROWSER", "none")
        env.setdefault("PORT", str(frontend_port))
        subprocess.run(
            ["npm", "start"],
            cwd=str(frontend_dir),
            env=env,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Failed to start frontend: {e}")

if __name__ == "__main__":
    # Start the backend with Gunicorn
    app = create_app()
    app.run(host="0.0.0.0", port=5000, threaded=True)
else:
    app = create_app()
    
