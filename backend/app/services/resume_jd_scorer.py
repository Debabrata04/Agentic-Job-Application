"""Loads kasraahmadi/job-eval-modernbert - a cross-encoder purpose-built for
resume<->JD relevance, as opposed to a generic sentence-pair model like
cross-encoder/ms-marco-MiniLM-L-6-v2 (tried first; empirically keyed on
surface "AI"-in-the-title keyword overlap and ranked worse than the bi-encoder
alone on real postings - see precision@k tests in project history).

Ships custom code (model.py) rather than a standard transformers/
sentence-transformers class, so this module handles the non-standard loading:
download model.py + config.json + weights from the HF repo, dynamically
import the ResumeJDScorer class, and construct it from config.json's
hyperparameters.

Scores 4 aspects (1-5 each): requirement_coverage, seniority_fit,
domain_alignment, ats_keyword_overlap. We average them and rescale to 0-1 to
blend into the rest of this project's composite score.
"""

import json
import os
import sys
from functools import lru_cache

import torch
from huggingface_hub import hf_hub_download
from safetensors.torch import load_model
from transformers import AutoTokenizer

REPO = "kasraahmadi/job-eval-modernbert"


@lru_cache(maxsize=1)
def _load():
    model_py_path = hf_hub_download(REPO, "model.py")
    model_dir = os.path.dirname(model_py_path)
    if model_dir not in sys.path:
        sys.path.insert(0, model_dir)
    from model import ResumeJDScorer  # type: ignore

    config_path = hf_hub_download(REPO, "config.json")
    with open(config_path) as f:
        cfg = json.load(f)

    tok = AutoTokenizer.from_pretrained(cfg["encoder"])
    model = ResumeJDScorer(
        name=cfg["encoder"],
        cls_id=tok.cls_token_id,
        sep_id=tok.sep_token_id,
        pad_id=tok.pad_token_id,
        label_mean=cfg["label_mean"],
        n_aspects=len(cfg["aspects"]),
        a_dim=cfg["a_dim"],
        hidden=cfg["hidden"],
        dropout=cfg["dropout"],
        n_train_layers=cfg["n_train_layers"],
        use_rel_logit=cfg["use_rel_logit"],
        attn=cfg["attn"],
        center=cfg["center_pooler"],
        temperature=cfg["temperature"],
        score_init=cfg["score_init"],
    )
    weights_path = hf_hub_download(REPO, "model.safetensors")
    load_model(model, weights_path)
    model.eval()
    return model, tok, cfg


def score_resume_jd_pairs(pairs: list[tuple[str, str]]) -> list[dict[str, float]]:
    """Returns the 4 raw 1-5 aspect scores for each (resume_text, jd_text) pair.

    Truncation (longest_first, up to config's max_len - 1024 tokens for the
    current checkpoint) is handled internally by the tokenizer, so callers
    don't need to pre-trim text themselves.
    """
    model, tok, cfg = _load()
    results = []
    with torch.no_grad():
        for resume_text, jd_text in pairs:
            enc = tok(
                resume_text, jd_text, truncation="longest_first", max_length=cfg["max_len"], return_tensors="pt"
            )
            scores = model(enc["input_ids"], enc["attention_mask"])[0].clamp(1, 5)
            results.append(dict(zip(cfg["aspects"], scores.tolist())))
    return results


def aggregate_score_0_1(aspect_scores: dict[str, float]) -> float:
    avg_1_to_5 = sum(aspect_scores.values()) / len(aspect_scores)
    return (avg_1_to_5 - 1) / 4  # rescale the model's 1-5 scale to 0-1
