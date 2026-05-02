#!/usr/bin/env python3
"""One-time export of the XLM-R encoder + trained head to ONNX / numpy.

This script runs **offline** (i.e. **not on-device**) and produces the
two artefacts shipped by the runtime adapter
(:mod:`kchat-skills/compiler/xlmr_adapter.py`):

1. ``models/xlmr.onnx`` — the XLM-R encoder exported via
   ``torch.onnx.export()`` and quantised to INT8 with
   ``onnxruntime.quantization``.
2. ``models/xlmr.spm`` — the SentencePiece tokenizer model file
   shipped alongside the encoder. The on-device adapter loads this
   directly through :mod:`sentencepiece` (no transformers required).

It also re-converts the trained linear head ``Linear(384, 16)``
produced by :mod:`train_xlmr_head` from a PyTorch ``state_dict``
(``.pt``) into a numpy ``.npz`` archive — the shape on-device:

    ``kchat-skills/compiler/data/xlmr_head.npz``  (keys: ``weight``,
    ``bias``).

Usage::

    # 1. Export ONNX + tokenizer + head .npz from a HuggingFace cache
    #    of `nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large`.
    pip install transformers torch onnx onnxruntime sentencepiece
    python tools/export_xlmr_onnx.py

    # 2. Override the source model id (offline / custom checkpoints).
    python tools/export_xlmr_onnx.py \
        --model-id nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large \
        --output-dir models

    # 3. Convert an existing trained head .pt only (skip ONNX export).
    python tools/export_xlmr_onnx.py --head-only \
        --head-pt kchat-skills/compiler/data/xlmr_head.pt \
        --head-npz kchat-skills/compiler/data/xlmr_head.npz

The runtime adapter loads the resulting ONNX file with
``onnxruntime.InferenceSession`` and the tokenizer with
``sentencepiece.SentencePieceProcessor``; PyTorch and transformers
are **not** required at runtime.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional


LOGGER = logging.getLogger("kchat.guardrail.export_xlmr_onnx")

DEFAULT_HF_MODEL_ID = "nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large"
DEFAULT_OUTPUT_DIR = Path("models")
DEFAULT_ONNX_NAME = "xlmr.onnx"
DEFAULT_ONNX_INT8_NAME = "xlmr.onnx"
DEFAULT_TOKENIZER_NAME = "xlmr.spm"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HEAD_PT = (
    REPO_ROOT / "kchat-skills" / "compiler" / "data" / "xlmr_head.pt"
)
DEFAULT_HEAD_NPZ = (
    REPO_ROOT / "kchat-skills" / "compiler" / "data" / "xlmr_head.npz"
)


# ---------------------------------------------------------------------------
# ONNX export.
# ---------------------------------------------------------------------------
def export_onnx(
    *,
    model_id: str,
    output_dir: Path,
    opset: int = 14,
    max_seq_length: int = 128,
    quantize_int8: bool = True,
) -> Path:
    """Export the HuggingFace XLM-R encoder to ONNX (optionally INT8).

    Returns the path to the final ONNX file (FP32 if quantisation was
    skipped, INT8 otherwise).
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    output_dir.mkdir(parents=True, exist_ok=True)
    onnx_fp32 = output_dir / "xlmr.fp32.onnx"
    onnx_final = output_dir / DEFAULT_ONNX_NAME
    tokenizer_dst = output_dir / DEFAULT_TOKENIZER_NAME

    LOGGER.info("Loading HuggingFace encoder %s", model_id)
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModel.from_pretrained(model_id)
    model.eval()

    # Locate the SentencePiece model file from the tokenizer and copy
    # it into the output directory. We support both the ``vocab_file``
    # attribute (slow tokenizer) and the ``sp_model_kwargs`` /
    # ``vocab_files_names`` paths (fast tokenizer).
    spm_src: Optional[Path] = None
    for attr in ("vocab_file", "_vocab_file"):
        candidate = getattr(tokenizer, attr, None)
        if candidate and Path(candidate).is_file():
            spm_src = Path(candidate)
            break
    if spm_src is None:
        raise RuntimeError(
            "Could not locate SentencePiece model file on the tokenizer. "
            "Re-run with the slow tokenizer (use_fast=False) or pass an "
            "explicit --tokenizer-spm path."
        )
    LOGGER.info("Copying tokenizer %s -> %s", spm_src, tokenizer_dst)
    shutil.copyfile(spm_src, tokenizer_dst)

    # Sample inputs for tracing. Use a non-empty string so the encoder
    # produces a meaningful graph; dynamic axes let downstream code
    # vary batch size and sequence length.
    dummy = tokenizer(
        "kchat guardrail xlm-r onnx export sample",
        return_tensors="pt",
        padding="max_length",
        truncation=True,
        max_length=max_seq_length,
    )

    LOGGER.info("Exporting FP32 ONNX -> %s", onnx_fp32)
    torch.onnx.export(
        model,
        (dummy["input_ids"], dummy["attention_mask"]),
        str(onnx_fp32),
        input_names=["input_ids", "attention_mask"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "seq"},
            "attention_mask": {0: "batch", 1: "seq"},
            "last_hidden_state": {0: "batch", 1: "seq"},
        },
        opset_version=opset,
        do_constant_folding=True,
    )

    if not quantize_int8:
        if onnx_fp32 != onnx_final:
            shutil.copyfile(onnx_fp32, onnx_final)
        return onnx_final

    LOGGER.info("Quantising ONNX -> INT8 -> %s", onnx_final)
    from onnxruntime.quantization import (  # type: ignore[import-not-found]
        QuantType,
        quantize_dynamic,
    )

    quantize_dynamic(
        model_input=str(onnx_fp32),
        model_output=str(onnx_final),
        weight_type=QuantType.QInt8,
    )

    # Drop the FP32 intermediate to keep the artifact directory tidy.
    try:
        onnx_fp32.unlink()
    except OSError:
        pass

    return onnx_final


# ---------------------------------------------------------------------------
# Head .pt -> .npz conversion.
# ---------------------------------------------------------------------------
def convert_head(
    *, pt_path: Path, npz_path: Path
) -> Path:
    """Convert a trained ``Linear(384, 16)`` ``state_dict`` to ``.npz``."""
    import numpy as np
    import torch

    LOGGER.info("Loading trained head state_dict from %s", pt_path)
    state = torch.load(pt_path, map_location="cpu", weights_only=True)

    weight = state["weight"].cpu().numpy().astype("float32")
    bias = state["bias"].cpu().numpy().astype("float32")

    if weight.shape != (16, 384):
        raise ValueError(
            f"Expected weight shape (16, 384); got {tuple(weight.shape)}"
        )
    if bias.shape != (16,):
        raise ValueError(
            f"Expected bias shape (16,); got {tuple(bias.shape)}"
        )

    npz_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(npz_path, weight=weight, bias=bias)
    LOGGER.info("Saved head -> %s", npz_path)
    return npz_path


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-time export of the XLM-R encoder to ONNX (INT8) and "
            "conversion of the trained head from PyTorch (.pt) to "
            "numpy (.npz). Runs offline; not used on-device."
        )
    )
    parser.add_argument(
        "--model-id",
        default=DEFAULT_HF_MODEL_ID,
        help=(
            "HuggingFace model id to load the encoder from "
            f"(default: {DEFAULT_HF_MODEL_ID})."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Directory to write xlmr.onnx + xlmr.spm into "
            f"(default: {DEFAULT_OUTPUT_DIR})."
        ),
    )
    parser.add_argument(
        "--max-seq-length",
        type=int,
        default=128,
        help="Max sequence length used for tracing (default: 128).",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=14,
        help="ONNX opset version (default: 14).",
    )
    parser.add_argument(
        "--no-quantize",
        action="store_true",
        help="Skip INT8 dynamic quantisation; ship FP32 instead.",
    )
    parser.add_argument(
        "--head-only",
        action="store_true",
        help="Only convert the trained head .pt -> .npz; skip the ONNX export.",
    )
    parser.add_argument(
        "--head-pt",
        type=Path,
        default=DEFAULT_HEAD_PT,
        help=(
            "Path to the trained head .pt produced by "
            "kchat-skills/compiler/train_xlmr_head.py "
            f"(default: {DEFAULT_HEAD_PT})."
        ),
    )
    parser.add_argument(
        "--head-npz",
        type=Path,
        default=DEFAULT_HEAD_NPZ,
        help=(
            "Output path for the head .npz consumed by XLMRAdapter "
            f"(default: {DEFAULT_HEAD_NPZ})."
        ),
    )
    parser.add_argument(
        "--no-head",
        action="store_true",
        help="Skip the head .pt -> .npz conversion entirely.",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if not args.head_only:
        export_onnx(
            model_id=args.model_id,
            output_dir=args.output_dir,
            opset=args.opset,
            max_seq_length=args.max_seq_length,
            quantize_int8=not args.no_quantize,
        )

    if not args.no_head:
        if not args.head_pt.is_file():
            LOGGER.warning(
                "Head .pt not found at %s; skipping head conversion. "
                "Run kchat-skills/compiler/train_xlmr_head.py first.",
                args.head_pt,
            )
        else:
            convert_head(pt_path=args.head_pt, npz_path=args.head_npz)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
