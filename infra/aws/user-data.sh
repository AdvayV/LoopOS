#!/bin/bash
# First-boot setup for the LoopOS GPU VM: Ollama (localhost only) + model + idle auto-terminate.
set -euxo pipefail
MODEL=qwen2.5-coder:7b
IDLE_CHECKS=6      # 6 checks x 5 min = 30 min without SSH -> shut down (terminates the instance)
MAX_HOURS=8        # hard cap, even if an SSH session is left open

# Ollama listens on 127.0.0.1:11434 by default; reach it through an SSH tunnel.
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable --now ollama
for i in $(seq 30); do ollama list >/dev/null 2>&1 && break; sleep 2; done
ollama pull "$MODEL"
touch /var/lib/loopos-ready

cat > /usr/local/bin/loopos-idle-check <<EOF
#!/bin/bash
up=\$(cut -d. -f1 /proc/uptime)
[ "\$up" -ge \$(( $MAX_HOURS * 3600 )) ] && shutdown -h now "LoopOS: max lifetime reached"
[ "\$up" -lt 1200 ] && exit 0
f=/run/loopos-idle
if [ "\$(ss -Htn state established '( sport = :22 )' | wc -l)" -gt 0 ]; then echo 0 > \$f; exit 0; fi
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
