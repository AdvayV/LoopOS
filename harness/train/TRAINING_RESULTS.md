# Verified LoopOS fine-tuning run

Base: `Qwen/Qwen2.5-Coder-1.5B-Instruct`. One epoch on verified teaching traces.
Used 82 training and 15 validation examples.
Validation loss: 1.1492 before; 0.1359 after.

Guided syscall checks (six prompts per model; no generated command is executed):
- Base: 5/6 valid requests; 5/6 exact guided requests.
- Fine-tuned: 6/6 valid requests; 4/6 exact guided requests.

These measurements do not establish autonomous task accuracy or exhaustive syscall correctness.
The data is scripted oracle demonstrations from 25 test-verified tasks, with validation split by task.
Prior verification: 94 harness tests, 31 real Ghostty CLI checks, and 12 training-specific checks passed.
Artifacts and raw logs were downloaded and checksum-verified; the Azure VM was deallocated.
The VM disk retains the merged model; disk and public IP charges may continue until resources are removed.
