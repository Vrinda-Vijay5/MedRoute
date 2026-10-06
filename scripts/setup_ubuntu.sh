#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch iperf3 python3-venv python3-pip \
  gcc libffi-dev libssl-dev libxml2-dev libxslt1-dev zlib1g-dev
sudo service openvswitch-switch start

python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install "setuptools<68"
python -m pip install --upgrade --no-build-isolation -r requirements.txt
python -m pip check
python -c 'from importlib.metadata import version; print("Python dependencies: Ryu={}, Eventlet={}, dnspython={}".format(version("ryu"), version("eventlet"), version("dnspython")))'
python scripts/patch_ryu_434_eventlet.py

python -m unittest discover -s tests -v
command -v ryu-manager
ryu-manager --version
command -v ovs-vsctl
sudo ovs-vsctl --timeout=5 show
command -v mn
mn --version
sudo mn -c
sudo mn --switch ovsbr --test pingall

echo "MedRoute prerequisites and local tests completed."
