# LoopOS GPU VM (AWS)

A throwaway GPU machine that serves the LoopOS worker model with Ollama. It is created when needed and deleted when finished, so it costs nothing while idle.

| Setting | Value |
|---|---|
| Region | `us-east-1` |
| Instance | `g4dn.xlarge` (NVIDIA T4, 16 GB), spot by default |
| Image | AWS Deep Learning Base OSS NVIDIA Driver GPU AMI (Ubuntu 24.04), latest via SSM |
| Disk | 75 GB gp3, encrypted, deleted with the instance |
| Model | `qwen2.5-coder:7b` via Ollama, listening on the VM's localhost only |
| Access | SSH from the owner's current IP only; Ollama reached through an SSH tunnel |
| Safety | Shuts down (and terminates) after 30 min with no SSH session, or after 8 hours |

## Usage

```bash
loopos-vm up            # launch (spot), wait until the model is ready
loopos-vm tunnel        # Ollama on http://localhost:11434
loopos-vm down          # terminate; billing stops
loopos-vm status | ssh
loopos-vm up --on-demand   # if no spot capacity
loopos-vm template      # after editing user-data.sh
```

## One-time account setup

These already exist in the project account; recreate them for a new account and update `SG` in `loopos-vm`.

1. GPU quota: Service Quotas → EC2 → "All G and VT Spot Instance Requests" and "Running On-Demand G and VT instances" ≥ 4 vCPUs.
2. Key pair `loopos-gpu` (private key at `~/.ssh/loopos-gpu.pem`, mode 400).
3. Security group `loopos-gpu-ssh` in the default VPC (SSH rule is refreshed to the current IP on every `up`).
4. `loopos-vm template` to create the launch template.
