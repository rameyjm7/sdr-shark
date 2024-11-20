# SDR-Shark
A React application with a Python backend for SIGINT applications with applied ML using PyTorch and TensorFlow

* Many changes will be coming soon *

This is an application I wrote before working on https://github.com/rameyjm7/ML-wireless-signal-classification

I will update the application to use some of these models and focus on performance improvements and building a foundation for a better app

For now,  An SDR is streamed from the backend (Python, SoapySDR) to the front end (React). 

![image](https://github.com/user-attachments/assets/b45a225d-c29a-41cd-ac24-9e92aef3b219)

Analysis is currently done using Python, and decision trees are used to classify signal types based on characteristics such as center frequency and bandwidth.

Plot controls such as Y-axis limits are available.

![image](https://github.com/user-attachments/assets/c4b52962-d1dd-48a3-9db2-ea66483c3e88)

The max trace (green), persistence trace (blue), and normal trace (yellow) are all available. The normal trace supports averaging.

Some statistics are ran on the signals

![image](https://github.com/user-attachments/assets/73b9d68d-9c32-48ad-99ab-bc1bd3c8c219)

There are band dictionaries for signals based on center frequency and bandwidth right now 

![image](https://github.com/user-attachments/assets/350209c7-25d9-4213-ab2a-a45eece924e4)

# How to install and run

apt install python3-venv

python3-venv /home/eng/python

source /home/eng/python/bin/activate


cd to the <repository root>/backend/ folder

pip install .

then for the backend, run

python3 -m sdr_plot_backend


for the frontend, in a new terminal, go the frontend folder and run these command to install the dependencies and run the frontend

yarn install

yarn start


