import faulthandler
import os
import subprocess
import threading
from pathlib import Path
from flask import Flask
from flask_cors import CORS
from sdr_plot_backend.actions import actions_blueprint
from sdr_plot_backend.api import api_blueprint
from sdr_plot_backend.file_manager import file_mgr_blueprint
from sdr_plot_backend.sigid_plugin import sigid_plugin_blueprint

faulthandler.enable()

def create_app():
    app = Flask(__name__)
    app.register_blueprint(api_blueprint)
    app.register_blueprint(actions_blueprint)
    app.register_blueprint(file_mgr_blueprint)
    app.register_blueprint(sigid_plugin_blueprint)
    CORS(app)
    # Start the frontend in a separate thread
    threading.Thread(target=start_frontend, daemon=True).start()
    return app

def start_frontend():
    """Launch the frontend using npm start."""
    auto_start = os.getenv("SDR_SHARK_AUTO_START_FRONTEND", "1").strip().lower()
    if auto_start in {"0", "false", "no"}:
        print("Frontend auto-start disabled by SDR_SHARK_AUTO_START_FRONTEND.")
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
    try:
        subprocess.run(
            ["npm", "start"],
            cwd=str(frontend_dir),
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
    
