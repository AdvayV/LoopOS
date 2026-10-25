# LoopOS CPU training on Azure

Use a dedicated resource group for an ephemeral trainer. Sign in with `az login` and explicitly
select the subscription with the intended credits before creating resources. Check the region's
VM-family and total vCPU quota, SKU restrictions, and the current Linux compute price.

The verified setup uses `Standard_E4as_v4` (4 vCPUs, 32 GiB), Ubuntu 24.04, and a 64 GiB
Standard SSD in East Asia. The checked student subscription had a six-vCPU regional quota,
a four-vCPU quota for this family, and zero quota for modern training GPUs in its five allowed
regions. Check those values again for another subscription. Use another region/size only after
checking its quota and price.
`cloud-init.sh` installs a CPU-only training environment in `/opt/loopos/train-venv`;
it writes `/var/lib/loopos-ready` or `/var/lib/loopos-setup-failed` and logs to
`/var/log/loopos-setup.log`. It does not start training automatically.

Create an SSH key specifically for this VM, restrict inbound TCP 22 to the operator's current
public IP, and keep model endpoints private. Configure Azure's VM auto-shutdown immediately
after creation, allowing enough time for setup plus the bounded training attempt.

After uploading the harness and verified dataset to the VM, run from the harness directory:

```bash
/opt/loopos/train-venv/bin/python train/run_once.py \
    --out train/out/job --max-seconds 14400 --update-seconds 30 -- \
    /opt/loopos/train-venv/bin/python -u train/finetune.py \
    --data train/out/data/loopos-dataset.jsonl \
    --val train/out/data/loopos-dataset.val.jsonl \
    --base Qwen/Qwen2.5-Coder-1.5B-Instruct --out train/out/model \
    --epochs 1 --max-length 2048 --threads 4 --batch 1 --accum 4 --merge
```

The runner records `training-status.json` and `training.log`, emits a heartbeat every 30 seconds,
locks out duplicate launches, and never automatically retries a failed or timed-out job.
The time limit includes downloads. Training completion does not establish model quality:
compare held-out results with the base model before claiming an improvement.

Retain the 1.5B base for this run. Its first measured four-example optimizer update took about
seven minutes, so this configuration allows four hours and uses a five-hour shutdown fallback.
Keep model capacity fixed when estimating runtime; reducing the model changes the experiment.

For a detached service, use `systemd-run` as the `loopos` user with this harness as the working
directory, `Restart=no`, `RuntimeMaxSec=14700`, and `MemoryMax=28G`, wrapping the same job command.
Read the status JSON and service result together to detect a memory kill or service timeout.
Before training, verify nonempty task-separated datasets, required action fields, duplicate
exclusion, tokenizer masking, at least one usable example after length filtering, and free disk.
Only the training-specific checks need to run again when the training path changes.

When resuming a deallocated VM, refresh the scheduled shutdown time and the SSH source-IP rule.
The operator's public IP can change; a stale `/32` rule will block SSH after an otherwise
successful restart. Preserve the dedicated SSH key and verify the instance host key.

Copy adapters, the merged model, training/validation metrics, and logs back locally before
deallocating the VM. Confirm Azure reports `VM deallocated`; a guest-OS shutdown alone does not
deallocate it. Disks and public IPs can continue to incur charges after deallocation. Once saved
artifacts are verified and the dedicated group is no longer needed, delete that group's resources.

The user's credit allowance is a spending ceiling, not a target. Check the actual subscription
credit balance and offer eligibility separately; retail estimates do not guarantee credit coverage.

References: [Linux VM creation](https://learn.microsoft.com/azure/virtual-machines/linux/quick-create-cli),
[VM shutdown scheduling](https://learn.microsoft.com/azure/virtual-machines/auto-shutdown-vm),
[Retail Prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices).
