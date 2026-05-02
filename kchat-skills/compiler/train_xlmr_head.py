"""Train the linear classification head for ``XLMRMiniLMAdapter``.

The trained head is a ``Linear(384, 16)`` layer fitted on top of the
**frozen** XLM-R MiniLM-L6 encoder. The encoder weights are not
modified; we only learn the projection from the mean-pooled token
embedding to the 16-category taxonomy.

Run from the repo root:

.. code-block:: bash

    python kchat-skills/compiler/train_xlmr_head.py

The script:

1. Loads the same encoder that ``XLMRMiniLMAdapter`` uses at runtime
   (``nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large``), with
   ``local_files_only=True`` so it works fully offline once cached.
2. Encodes every example in :data:`training_data.TRAINING_EXAMPLES`
   into a 384-dim L2-normalised mean-pooled vector.
3. Fits a ``Linear(384, 16)`` head with AdamW + class-weighted
   cross-entropy. SAFE is over-represented in the corpus, so we
   downweight it to keep harm classes well-calibrated.
4. Validates training-set accuracy and per-class accuracy.
5. Saves the head state_dict to
   ``kchat-skills/compiler/data/xlmr_minilm_head.pt`` along with a
   small JSON sidecar describing the encoder revision and training
   metadata. Reviewers can audit both files.

Determinism: torch + python random seeds are pinned. Re-running the
script on the same encoder + same corpus produces an identical
state_dict (modulo CPU floating-point non-determinism, which we
mitigate with ``torch.use_deterministic_algorithms(True)``).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

# Allow ``python kchat-skills/compiler/train_xlmr_head.py`` to find the
# sibling modules without requiring an installed package.
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from training_data import TRAINING_EXAMPLES, category_counts  # noqa: E402

LOGGER = logging.getLogger("kchat.guardrail.train_xlmr_head")

XLMR_MINILM_MODEL_ID = "nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large"
TAXONOMY_SIZE = 16
EMBED_DIM = 384

DATA_DIR = _THIS_DIR / "data"
HEAD_WEIGHTS_PATH = DATA_DIR / "xlmr_minilm_head.pt"
HEAD_METADATA_PATH = DATA_DIR / "xlmr_minilm_head.json"


# ---------------------------------------------------------------------------
# Encoder / corpus loading.
# ---------------------------------------------------------------------------
def _set_seeds(seed: int = 7) -> None:
    """Pin RNG seeds for reproducibility."""
    random.seed(seed)
    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(True, warn_only=True)
    except ImportError:
        pass


def _encode_corpus(
    *,
    model_id: str = XLMR_MINILM_MODEL_ID,
    local_files_only: bool = False,
    max_seq_length: int = 128,
) -> tuple[Any, Any, str]:
    """Encode every training example and return ``(X, y, encoder_revision)``.

    ``X`` is an ``(n, 384)`` float tensor of L2-normalised mean-pooled
    embeddings. ``y`` is an ``(n,)`` long tensor of category ids.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    LOGGER.info("Loading encoder %s (local_files_only=%s)", model_id, local_files_only)
    tokenizer = AutoTokenizer.from_pretrained(
        model_id, local_files_only=local_files_only
    )
    model = AutoModel.from_pretrained(
        model_id, local_files_only=local_files_only
    )
    model.eval()

    texts = [t for t, _ in TRAINING_EXAMPLES]
    labels = [c for _, c in TRAINING_EXAMPLES]

    embeddings: list[Any] = []
    batch_size = 32
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            encoded = tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=max_seq_length,
                return_tensors="pt",
            )
            output = model(**encoded)
            last_hidden = output.last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).float()
            summed = (last_hidden * mask).sum(dim=1)
            counts = mask.sum(dim=1).clamp(min=1.0)
            pooled = summed / counts
            norms = pooled.norm(dim=1, keepdim=True).clamp(min=1e-12)
            embeddings.append(pooled / norms)

    X = torch.cat(embeddings, dim=0).contiguous()
    y = torch.tensor(labels, dtype=torch.long)
    rev = getattr(model.config, "_commit_hash", "") or "unknown"
    return X, y, rev


# ---------------------------------------------------------------------------
# Trainer.
# ---------------------------------------------------------------------------
def _train_head(
    X: Any,
    y: Any,
    *,
    epochs: int = 200,
    lr: float = 5e-3,
    weight_decay: float = 1e-3,
) -> Any:
    """Fit ``Linear(384, 16)`` with class-weighted cross-entropy.

    Returns the trained ``torch.nn.Linear`` module (eval mode).
    """
    import torch
    from torch import nn

    head = nn.Linear(EMBED_DIM, TAXONOMY_SIZE, bias=True)
    nn.init.xavier_uniform_(head.weight)
    nn.init.zeros_(head.bias)

    # Class-weighted CE — SAFE is over-represented (25 vs 10), and the
    # zero-shot prototype already biases toward SAFE; we down-weight
    # SAFE so the harm classes remain calibrated.
    counts = torch.bincount(y, minlength=TAXONOMY_SIZE).float()
    inverse = (counts.sum() / (counts.clamp(min=1.0) * TAXONOMY_SIZE))
    class_weights = inverse / inverse.sum() * TAXONOMY_SIZE
    LOGGER.info("Class weights: %s", class_weights.tolist())

    optimizer = torch.optim.AdamW(
        head.parameters(), lr=lr, weight_decay=weight_decay
    )
    loss_fn = nn.CrossEntropyLoss(weight=class_weights)

    head.train()
    for epoch in range(1, epochs + 1):
        optimizer.zero_grad()
        logits = head(X)
        loss = loss_fn(logits, y)
        loss.backward()
        optimizer.step()
        if epoch == 1 or epoch % 25 == 0 or epoch == epochs:
            with torch.no_grad():
                preds = logits.argmax(dim=1)
                acc = (preds == y).float().mean().item()
            LOGGER.info(
                "epoch=%4d  loss=%.4f  train_acc=%.3f", epoch, loss.item(), acc
            )

    head.eval()
    return head


def _per_class_report(head: Any, X: Any, y: Any) -> dict[int, float]:
    """Return ``{category: training-set accuracy}``."""
    import torch

    with torch.no_grad():
        preds = head(X).argmax(dim=1)
    out: dict[int, float] = {}
    for c in range(TAXONOMY_SIZE):
        mask = y == c
        n = int(mask.sum().item())
        if n == 0:
            continue
        out[c] = float(((preds == c) & mask).sum().item() / n)
    return out


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train the XLM-R MiniLM-L6 classification head and save "
            "state_dict + metadata to kchat-skills/compiler/data/."
        )
    )
    parser.add_argument(
        "--epochs", type=int, default=200, help="Number of training epochs."
    )
    parser.add_argument("--lr", type=float, default=5e-3, help="Learning rate.")
    parser.add_argument(
        "--weight-decay", type=float, default=1e-3, help="L2 regularisation."
    )
    parser.add_argument(
        "--seed", type=int, default=7, help="RNG seed for reproducibility."
    )
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Load encoder weights from the HF cache only (offline).",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    LOGGER.info("Training corpus size: %d", len(TRAINING_EXAMPLES))
    LOGGER.info("Per-class counts: %s", category_counts())

    _set_seeds(args.seed)

    t0 = time.time()
    X, y, encoder_revision = _encode_corpus(
        local_files_only=args.local_files_only,
    )
    LOGGER.info(
        "Encoded %d examples to (%d, %d) in %.1fs (encoder rev=%s)",
        X.shape[0],
        X.shape[0],
        X.shape[1],
        time.time() - t0,
        encoder_revision,
    )

    head = _train_head(
        X,
        y,
        epochs=args.epochs,
        lr=args.lr,
        weight_decay=args.weight_decay,
    )

    accuracies = _per_class_report(head, X, y)
    overall = sum(accuracies.values()) / max(1, len(accuracies))
    LOGGER.info(
        "Training-set accuracy by class: %s (mean=%.3f)", accuracies, overall
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    import torch

    torch.save(head.state_dict(), HEAD_WEIGHTS_PATH)
    metadata = {
        "encoder_model_id": XLMR_MINILM_MODEL_ID,
        "encoder_revision": encoder_revision,
        "embed_dim": EMBED_DIM,
        "taxonomy_size": TAXONOMY_SIZE,
        "training_corpus_size": len(TRAINING_EXAMPLES),
        "training_corpus_per_class": category_counts(),
        "training_epochs": args.epochs,
        "training_lr": args.lr,
        "training_weight_decay": args.weight_decay,
        "training_seed": args.seed,
        "training_set_accuracy_per_class": accuracies,
        "training_set_accuracy_mean": overall,
    }
    HEAD_METADATA_PATH.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    LOGGER.info("Saved head weights -> %s", HEAD_WEIGHTS_PATH)
    LOGGER.info("Saved head metadata -> %s", HEAD_METADATA_PATH)

    if not math.isfinite(overall) or overall < 0.85:
        LOGGER.warning(
            "Training-set accuracy below 0.85 (%.3f); review the corpus.",
            overall,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
