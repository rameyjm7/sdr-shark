# SDR-Shark
A React application with a Python backend for SIGINT applications with applied ML using PyTorch and TensorFlow

Signal Intelligence platform at your fingertips...
<img width="1663" height="922" alt="image" src="https://github.com/user-attachments/assets/1ac44e57-5adc-4a71-ba10-3a4783bd558f" />


# How to install

apt install python3-venv

python3-venv /home/eng/python

source /home/eng/python/bin/activate


#cd to the <repository root>/backend/ folder

pip install .


#for the frontend, in a new terminal, go the frontend folder and run these command to install the dependencies and run the frontend

yarn install

# How to run

**Option 1:** (Recommended for speed): Gunicorn using scripts/start.sh

From the repository root after installing the dependencies (backend and frontend), run ./scripts/start.sh to run the gunicorn server.

Systemd service helper:

Use ./scripts/sdr-shark-service.sh to install/manage a service:

- install
- enable
- disable
- start
- stop
- restart
- status
- logs

If your `sdr-gateway` lives somewhere other than `http://127.0.0.1:8080`, set `SDR_SERVER_URL` before installing the service and the helper will write it into the service environment file alongside `SDR_GATEWAY_API_TOKEN`.


**Option 2:** If you want to make changes and develop

#run the frontend from the frontend folder
yarn start

#run the backend using python3
python3 -m sdr_plot_backend

Then, Browse to the IP Address of your PC, port 3000. i.e. 10.139.1.86:3000
