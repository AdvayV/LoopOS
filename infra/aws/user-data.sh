#!/bin/bash
# First-boot setup for the LoopOS CPU VM: Ollama (localhost only) + model, Python tooling for
# training, and an idle check that shuts the VM down (which terminates it).
set -euxo pipefail
MODEL=qwen2.5-coder:3b
IDLE_CHECKS=6      # 6 checks x 5 min = 30 min idle -> shut down
MAX_HOURS=8        # hard cap; 24 while `loopos-vm busy on` (e.g. a training run)

export DEBIAN_FRONTEND=noninteractive
export HOME=/root   # cloud-init runs without it and `ollama pull` panics on an unset $HOME
apt-get update
apt-get install -y python3-venv python3-pip python3-dev build-essential git htop

# Ollama listens on 127.0.0.1:11434 by default; reach it through an SSH tunnel.
curl -fsSL https://ollama.com/install.sh | sh
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/loopos.conf <<'EOF'
[Service]
# Keep the model loaded between loop iterations; one request at a time (LoopOS schedules turns).
Environment=OLLAMA_KEEP_ALIVE=-1
Environment=OLLAMA_NUM_PARALLEL=1
EOF
systemctl daemon-reload
systemctl enable --now ollama
systemctl restart ollama
for i in $(seq 30); do ollama list >/dev/null 2>&1 && break; sleep 2; done
ollama pull "$MODEL"
touch /var/lib/loopos-ready

cat > /usr/local/bin/loopos-idle-check <<EOF
#!/bin/bash
# Idle = no SSH connection (an open tunnel counts as one) and no busy flag.
up=\$(cut -d. -f1 /proc/uptime)
busy=0; [ -e /var/lib/loopos-busy ] && busy=1
cap=\$(( busy ? 24 : $MAX_HOURS ))
[ "\$up" -ge \$(( cap * 3600 )) ] && shutdown -h now "LoopOS: max lifetime (\${cap}h) reached"
[ "\$up" -lt 1200 ] && exit 0
f=/run/loopos-idle
if [ \$busy = 1 ] || [ "\$(ss -Htn state established '( sport = :22 )' | wc -l)" -gt 0 ]; then echo 0 > \$f; exit 0; fi
n=\$(( \$(cat \$f 2>/dev/null || echo 0) + 1 )); echo \$n > \$f
[ \$n -ge $IDLE_CHECKS ] && shutdown -h now "LoopOS: idle with no SSH sessions"
exit 0
EOF
chmod +x /usr/local/bin/loopos-idle-check

cat > /etc/systemd/system/loopos-idle.service <<'EOF'
[Unit]
Description=LoopOS idle shutdown check
[Service]
Type=oneshot
ExecStart=/usr/local/bin/loopos-idle-check
EOF
cat > /etc/systemd/system/loopos-idle.timer <<'EOF'
[Unit]
Description=Run LoopOS idle check every 5 minutes
[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now loopos-idle.timer
