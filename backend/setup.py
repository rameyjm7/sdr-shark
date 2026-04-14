from setuptools import setup, find_packages

setup(
    name="sdr_plot_backend",
    version="0.1.0",
    description="SDR plot backend",
    author="Jacob Ramey",
    author_email="rameyjm7@gmail.com",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "flask",
        "numpy",
        "websocket-client",
        "requests",
        "bluetooth_demod",
    ],
    entry_points={
        "console_scripts": [
            "start=sdr_plot_backend.__init__:create_app",
        ],
    },
)
