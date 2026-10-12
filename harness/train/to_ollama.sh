#!/bin/bash
# Turn a merged Hugging Face model (train/finetune.py --merge) into an Ollama model.
#   train/to_ollama.sh train/out/merged [NAME] [QUANT]      (default: loopos-worker, q4_K_M)
# Needs: git, python3, ollama. llama.cpp's converter is fetched into ~/.cache/loopos/llama.cpp.
set -euo pipefail
MERGED=${1:?usage: to_ollama.sh MERGED_DIR [NAME] [QUANT]}
NAME=${2:-loopos-worker}
QUANT=${3:-q4_K_M}
LLAMA=${LLAMA_CPP:-$HOME/.cache/loopos/llama.cpp}

[ -f "$MERGED/config.json" ] || { echo "no model in $MERGED (run finetune.py --merge first)" >&2; exit 1; }
if [ ! -d "$LLAMA" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA"
fi
if [ ! -x "$LLAMA/.venv/bin/python" ]; then
  python3 -m venv "$LLAMA/.venv"
  "$LLAMA/.venv/bin/pip" install -q -r "$LLAMA/requirements/requirements-convert_hf_to_gguf.txt"
fi

GGUF=$MERGED/model-f16.gguf
"$LLAMA/.venv/bin/python" "$LLAMA/convert_hf_to_gguf.py" "$MERGED" --outfile "$GGUF" --outtype f16
cat > "$MERGED/Modelfile" <<EOF
FROM ./model-f16.gguf
PARAMETER temperature 0.2
PARAMETER num_ctx 8192
EOF
ollama create "$NAME" --quantize "$QUANT" -f "$MERGED/Modelfile"
echo
echo "Created Ollama model '$NAME' ($QUANT). Use it with:   loopos model $NAME"
echo "Compare against the base model on held-out tasks:   loopos bench <project>-holdout"
