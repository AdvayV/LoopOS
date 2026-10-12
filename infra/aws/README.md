# LoopOS CPU VM (AWS)

A throwaway compute-optimised machine that serves the LoopOS worker model with Ollama and runs training jobs. It is created when needed and deleted when finished, so it costs nothing while idle.

| Setting | Value |
|---|---|
| Region | `us-east-1` |
| Instance | `c7i.2xlarge` (8 vCPU, 16 GB), spot by default; `--type` or `$LOOPOS_VM_TYPE` for larger |
| Image | Ubuntu 24.04 LTS, latest via SSM |
| Disk | 40 GB gp3, encrypted, deleted with the instance |
| Model | `qwen2.5-coder:3b` via Ollama, listening on the VM's localhost only; `loopos-vm pull` adds more |
| Access | SSH from the owner's current IP only; Ollama reached through an SSH tunnel |
| Safety | Shuts down (and terminates) after 30 min with no SSH session, or after 8 hours; `busy on` keeps it up for up to 24 hours |

## Usage

```bash
loopos-vm up                       # launch (spot), wait until the model is ready
loopos-vm tunnel                   # Ollama on http://localhost:11434
loopos-vm down                     # terminate; billing stops
loopos-vm up --type c7i.8xlarge    # more vCPUs (needs a matching quota)
loopos-vm pull qwen2.5-coder:7b
loopos-vm push data.jsonl          # copy to the VM; `fetch` copies back. Fetch results before `down`.
loopos-vm busy on|off              # keep alive without SSH, e.g. during training
loopos-vm status | ssh [CMD]
loopos-vm template                 # after editing user-data.sh
```

## One-time account setup

These already exist in the project account; recreate them for a new account and update `SG` in `loopos-vm`.

1. The account must be on a paid plan: Free-plan accounts can only launch free-tier types (2 vCPU).
2. vCPU quota: Service Quotas → EC2 → "All Standard (A, C, D, H, I, M, R, T, Z) Spot Instance Requests" and "Running On-Demand Standard ... instances" (default 8; 32 requested).
3. Key pair `loopos-vm` (private key at `~/.ssh/loopos-vm.pem`, mode 400).
4. Security group `loopos-vm-ssh` in the default VPC (SSH rule is refreshed to the current IP on every `up`).
5. `loopos-vm template` to create the launch template.
