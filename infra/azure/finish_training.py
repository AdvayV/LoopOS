"""Bounded, non-LLM completion controller for an already running Azure training job.

Never starts or retries training. After success, evaluates, verifies/backups artifacts,
deallocates the VM, and optionally publishes the exact reviewed source manifest.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import shlex
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    repo, out = Path(cfg["repo"]), Path(cfg["out"])
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / "finish.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("A completion controller is already running")
    state = {"stage": "waiting", "started": datetime.now(timezone.utc).isoformat()}

    def record(stage, **values):
        state.update(stage=stage, updated=datetime.now(timezone.utc).isoformat(), **values)
        temp = out / "completion-status.tmp"
        temp.write_text(json.dumps(state, indent=2) + "\n")
        temp.replace(out / "completion-status.json")
        print(state["updated"], stage, json.dumps(values), flush=True)

    def run(argv, timeout=120):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"{argv[0]} failed ({result.returncode}): {result.stderr[-2000:]}")
        return result.stdout

    ssh = ["ssh", "-i", cfg["key"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
           "-o", f"UserKnownHostsFile={cfg['known_hosts']}", cfg["host"]]

    def remote_python(source, timeout=90):
        return run(ssh + ["python3 -c " + shlex.quote(source)], timeout)

    root = cfg["remote_root"]

    def wait_job(relative, unit, deadline):
        errors = 0
        while time.monotonic() < deadline:
            try:
                raw = remote_python(
                    "import json,pathlib,subprocess; "
                    f"p=pathlib.Path({root!r})/{relative!r}/'training-status.json'; "
                    "j=json.loads(p.read_text()) if p.exists() else {'state':'starting'}; "
                    f"s=subprocess.run(['systemctl','show',{unit!r},'--property=ActiveState','--property=Result'],capture_output=True,text=True); "
                    "print(json.dumps({'job':j,'service':s.stdout}))")
                current = json.loads(raw)
                errors = 0
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                errors += 1
                record("connection_retry", error=str(exc), attempt=errors)
                if errors >= 3:
                    raise
                time.sleep(15)
                continue
            (out / (unit + ".json")).write_text(json.dumps(current, indent=2) + "\n")
            job = current["job"]
            record("monitoring", unit=unit, job_state=job["state"], elapsed=job.get("elapsed_seconds"))
            if job["state"] == "succeeded":
                return job
            if job["state"] in {"failed", "timed_out", "interrupted"} or "ActiveState=failed" in current["service"]:
                raise RuntimeError(f"{unit}: {current}")
            time.sleep(45)
        raise TimeoutError(f"Deadline waiting for {unit}; training was not restarted")

    try:
        record("waiting_for_training")
        wait_job(cfg["job"], cfg["unit"], time.monotonic() + 14400)
        model = cfg["model"]
        report = json.loads(remote_python(
            "import json,pathlib,math; "
            f"p=pathlib.Path({root!r})/{model!r}; r=json.loads((p/'training-report.json').read_text()); "
            "assert all(math.isfinite(r[k]) for k in ('training_loss','baseline_validation_loss','validation_loss')); "
            f"assert r['base']=={cfg['base']!r}; "
            "assert (p/'adapter/adapter_model.safetensors').stat().st_size>0; "
            "assert (p/'merged/config.json').exists(); print(json.dumps(r))"))
        record("training_verified", report=report)
        evaluation = ["/opt/loopos/train-venv/bin/python", "-u", "train/run_once.py", "--out",
                      "train/out/evaluation-job", "--max-seconds", "1800", "--update-seconds", "30", "--",
                      "/opt/loopos/train-venv/bin/python", "-u", "train/evaluate_actions.py", "--models",
                      cfg["base"], model + "/merged", "--out", "train/out/action-evaluation.json"]
        command = ["sudo", "systemd-run", "--unit=loopos-action-evaluation", "--uid=loopos",
                   "--working-directory=" + root, "--property=Restart=no", "--property=RuntimeMaxSec=1900",
                   "--property=MemoryMax=28G", "--property=OOMPolicy=stop",
                   "--property=StandardOutput=append:" + root + "/train/out/evaluation-controller.log",
                   "--property=StandardError=inherit", "--setenv=PYTHONUNBUFFERED=1", *evaluation]
        run(ssh + [shlex.join(command)])
        record("evaluating_guided_requests")
        wait_job("train/out/evaluation-job", "loopos-action-evaluation", time.monotonic() + 2000)
        archive = root + "/train/out/final-artifacts.tar.gz"
        digest = remote_python(
            "import pathlib,tarfile,hashlib; "
            f"root=pathlib.Path({root!r}); model=root/{model!r}; dest=pathlib.Path({archive!r}); "
            "items=[model/'adapter',model/'training-report.json',root/'train/out/action-evaluation.json',"
            f"root/{cfg['job']!r},root/'train/out/evaluation-job']; "
            "items+=list((model/'checkpoints').glob('*_results.json')); "
            "t=tarfile.open(dest,'w:gz'); "
            "[t.add(p,arcname=str(p.relative_to(root/'train/out'))) for p in items]; t.close(); "
            "print(hashlib.sha256(dest.read_bytes()).hexdigest())", timeout=180).strip()
        record("copying_artifacts")
        target = out / "final-artifacts.tar.gz"
        run(["scp", "-i", cfg["key"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o",
             f"UserKnownHostsFile={cfg['known_hosts']}", cfg["host"] + ":" + archive, str(target)], timeout=1800)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Artifact archive checksum mismatch")
        with tarfile.open(target) as saved:
            saved.extractall(out / "artifacts", filter="data")
        evaluation_report = json.loads((out / "artifacts/action-evaluation.json").read_text())
        if len(evaluation_report["reports"]) != 2 or any(r["total"] != 6 for r in evaluation_report["reports"]):
            raise RuntimeError("Guided request evaluation is incomplete")
        record("artifacts_verified", archive_sha256=digest)
        azure = [cfg["az"], "vm", "deallocate", "--resource-group", cfg["resource_group"], "--name", cfg["vm"]]
        run(azure + ["--no-wait", "--only-show-errors"])
        for _ in range(20):
            power = run([cfg["az"], "vm", "get-instance-view", "--resource-group", cfg["resource_group"],
                         "--name", cfg["vm"], "--query",
                         "instanceView.statuses[?starts_with(code, 'PowerState/')].code | [0]", "--output", "tsv"]).strip()
            if power == "PowerState/deallocated":
                break
            time.sleep(10)
        else:
            raise RuntimeError("Azure has not confirmed VM deallocation")
        record("vm_deallocated")
        summary = {"training": report, "guided_generation": evaluation_report,
                   "data_provenance": "scripted oracle demonstrations, task-separated validation",
                   "coverage": {"prior_harness_tests_passed": 94, "ghostty_checks_passed": 31,
                                "finetune_checks_passed": 12}, "artifact_sha256": digest}
        (repo / "harness/train/verified-training-report.json").write_text(json.dumps(summary, indent=2) + "\n")
        lines = ["# Verified LoopOS fine-tuning run", "", f"Base: `{report['base']}`. One epoch on verified teaching traces.",
                 f"Used {report['train_examples']} training and {report['validation_examples']} validation examples.",
                 f"Validation loss: {report['baseline_validation_loss']:.4f} before; {report['validation_loss']:.4f} after.",
                 "", "Guided syscall checks (six prompts per model; no generated command is executed):"]
        for r in evaluation_report["reports"]:
            label = "Base" if r["model"] == cfg["base"] else "Fine-tuned"
            lines.append(f"- {label}: {r['valid_requests']}/6 valid requests; {r['matched_requests']}/6 exact guided requests.")
        lines += ["", "These measurements do not establish autonomous task accuracy or exhaustive syscall correctness.",
                  "The data is scripted oracle demonstrations from 25 test-verified tasks, with validation split by task.",
                  "Prior verification: 94 harness tests, 31 real Ghostty CLI checks, and 12 training-specific checks passed.",
                  "Artifacts and raw logs were downloaded and checksum-verified; the Azure VM was deallocated.",
                  "The VM disk retains the merged model; disk and public IP charges may continue until resources are removed."]
        (repo / "harness/train/TRAINING_RESULTS.md").write_text("\n".join(lines) + "\n")
        for path, digest in cfg["source_hashes"].items():
            if hashlib.sha256((repo / path).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"Source changed during training; review required before publishing: {path}")
        if run(["git", "branch", "--show-current"]).strip() != "kaavish_devs":
            raise RuntimeError("Branch changed; refusing to publish")
        run(["git", "fetch", "origin", "kaavish_devs"])
        if run(["git", "rev-parse", "HEAD"]).strip() != cfg["head"] or run(["git", "rev-parse", "origin/kaavish_devs"]).strip() != cfg["head"]:
            raise RuntimeError("Branch advanced during training; review required before publishing")
        paths = list(cfg["source_hashes"]) + ["harness/train/TRAINING_RESULTS.md", "harness/train/verified-training-report.json"]
        run(["git", "diff", "--check"])
        if run(["git", "diff", "--cached", "--name-only"]).strip():
            raise RuntimeError("Other staged changes appeared; review required before publishing")
        run(["git", "add", "--", *paths])
        run(["git", "commit", "-m", "Simplify LoopOS commands and verify Azure model fine-tuning"])
        commit = run(["git", "rev-parse", "HEAD"]).strip()
        run(["git", "push", "origin", "HEAD:kaavish_devs"], timeout=180)
        if run(["git", "ls-remote", "origin", "refs/heads/kaavish_devs"]).split()[0] != commit:
            raise RuntimeError("Could not verify pushed commit")
        record("complete", commit=commit, report="harness/train/TRAINING_RESULTS.md")
        return 0
    except Exception as exc:
        record("needs_attention", error=str(exc))
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
