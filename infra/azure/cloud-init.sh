#!/bin/bash
# First boot only: prepare a CPU trainer. No daemon or training job starts automatically.
set -euo pipefail
exec > /var/log/loopos-setup.log 2>&1
export DEBIAN_FRONTEND=noninteractive
trap 'touch /var/lib/loopos-setup-failed' ERR
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev build-essential git curl ca-certificates
mkdir -p /opt/loopos
python3 -m venv /opt/loopos/train-venv
timeout 1200 /opt/loopos/train-venv/bin/pip install --disable-pip-version-check \
    'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu
timeout 600 /opt/loopos/train-venv/bin/pip install --disable-pip-version-check \
    'transformers>=4.45,<5' 'peft>=0.12,<0.19' 'accelerate>=0.33,<2' 'pytest>=8.3'
chown -R loopos:loopos /opt/loopos
touch /var/lib/loopos-ready
