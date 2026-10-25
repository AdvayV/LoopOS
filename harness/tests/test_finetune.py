"""Training data and token masking checks; no model downloads or CLI execution."""
import json

import pytest

from train import finetune


class Tokenizer:
    def apply_chat_template(self, messages, add_generation_prompt=False, tokenize=False):
        text = "".join(f"{m['role']}:{m['content']}\n" for m in messages)
        return text + ("assistant:" if add_generation_prompt else "")

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": [ord(c) for c in text]}


MESSAGES = [{"role": "user", "content": "request"}, {"role": "assistant", "content": "answer"}]


def test_mask_only_assistant_tokens_and_skip_long_examples():
    tokenizer = Tokenizer()
    encoded = finetune.encode(tokenizer, MESSAGES, 1000)
    prompt = tokenizer.apply_chat_template(MESSAGES[:-1], add_generation_prompt=True)
    assert encoded["labels"][:len(prompt)] == [-100] * len(prompt)
    assert encoded["labels"][len(prompt):] == list(map(ord, "answer\n"))
    assert len(encoded["labels"]) == len(encoded["input_ids"]) == len(encoded["attention_mask"])
    assert finetune.encode(tokenizer, MESSAGES, 3) is None


def test_token_boundary_changes_fail_clearly():
    class ChangedBoundary(Tokenizer):
        def __call__(self, text, add_special_tokens=False):
            ids = super().__call__(text)["input_ids"]
            if "answer" in text:
                ids[0] += 1
            return {"input_ids": ids}

    with pytest.raises(ValueError, match="tokenized prompt is not a prefix"):
        finetune.encode(ChangedBoundary(), MESSAGES, 1000)


@pytest.mark.parametrize("row", [
    "not json", "{}", '{"messages": []}',
    json.dumps({"messages": MESSAGES[:-1]}),
    json.dumps({"messages": [MESSAGES[0], {"role": "assistant", "content": ""}]}),
])
def test_malformed_rows_report_file_and_line(tmp_path, row):
    source = tmp_path / "bad.jsonl"
    source.write_text("\n" + row + "\n")
    with pytest.raises(ValueError, match=r"bad.jsonl:2: invalid training example"):
        finetune.read_examples(source)


def test_blank_lines_limits_and_empty_inputs(tmp_path):
    source = tmp_path / "data.jsonl"
    source.write_text("\n" + json.dumps({"messages": MESSAGES}) + "\n" + json.dumps({"messages": MESSAGES}) + "\n")
    assert finetune.read_examples(source, 1) == [MESSAGES]
    assert len(finetune.read_examples(source)) == 2
    source.write_text("")
    assert finetune.read_examples(source) == []


@pytest.mark.parametrize("flag,value", [("--epochs", "nan"), ("--epochs", "0"),
                                        ("--batch", "0"), ("--limit", "-1")])
def test_invalid_training_options_are_rejected_before_loading_model(flag, value):
    with pytest.raises(SystemExit) as exc:
        finetune.main(["--data", "not-read.jsonl", flag, value])
    assert exc.value.code == 2
