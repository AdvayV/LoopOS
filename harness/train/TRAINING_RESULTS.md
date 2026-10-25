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

## Verification before merging main — 2026-10-25

Fresh checks passed: 107 harness tests, 18 backend tests, 12 frontend tests, 51 example-project tests, and 31 checks in a real Ghostty terminal. The frontend production build and infrastructure shell syntax checks passed.

A collection failure in the normal pytest-only environment was fixed by loading optional ML dependencies when training starts. The data and token-masking helpers now run without installing PyTorch. A runtime check on Azure confirmed the dataset works with the real PyTorch DataLoader and that Transformers training arguments still load correctly.

[GitHub CI passed on Python 3.11 and 3.12](https://github.com/AdvayV/LoopOS/actions/runs/40207568497).

A fresh comparison against the same saved 1.5B model reproduced the earlier results: base 5/6 valid and 5/6 exact; fine-tuned 6/6 valid and 4/6 exact. The fine-tuned model requested a read for the guided edit request and a directory listing for the completion request. These remain known limitations; no overall accuracy improvement is established. The model was not retrained during verification. Azure VM deallocation was confirmed after the checks.
