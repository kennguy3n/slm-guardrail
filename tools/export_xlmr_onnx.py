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
    #
    #    The export uses the legacy (non-dynamo) tracer path
    #    (``torch.onnx.export(..., dynamo=False)``) — the dynamo
    #    exporter on torch >= 2.5 emits an INT8 graph with
    #    ``tensor(float16)`` ``DequantizeLinear`` scales that
    #    ``onnxruntime`` rejects, and the FP32 graph it produces
    #    breaks dynamic-shape ``scaled_dot_product_attention`` at
    #    inference. The legacy tracer requires ``transformers<5``
    #    because v5 changed ``XLMRobertaModel.forward()`` so the
    #    positional ``(input_ids, attention_mask)`` trace fails with
    #    ``got multiple values for argument 'use_cache'``.
    pip install -e ".[export]"
    # or: pip install "transformers<5" torch onnx onnxruntime \
    #         sentencepiece onnxscript
    python tools/export_xlmr_onnx.py

    # 2. Override the source model id (offline / custom checkpoints).
    python tools/export_xlmr_onnx.py \
        --model-id nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large \
        --output-dir models

    # 3. Convert an existing trained head .pt only (skip ONNX export).
    python tools/export_xlmr_onnx.py --head-only \
        --head-pt kchat-skills/compiler/data/xlmr_head.pt \
        --head-npz kchat-skills/compiler/data/xlmr_head.npz

    # 4. Additionally produce an INT4 (block-wise weight-only) ONNX
    #    file at models/xlmr.int4.onnx (~55 MB vs ~107 MB INT8) and
    #    validate it against the INT8 graph by asserting per-row
    #    cosine similarity above ``--int4-min-cosine`` (default 0.94)
    #    on a multilingual smoke corpus.
    python tools/export_xlmr_onnx.py \
        --quantize-int4 --validate-int4 --output-dir models

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
from typing import Any, Optional


LOGGER = logging.getLogger("kchat.guardrail.export_xlmr_onnx")

DEFAULT_HF_MODEL_ID = "nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large"
DEFAULT_OUTPUT_DIR = Path("models")
DEFAULT_ONNX_NAME = "xlmr.onnx"
DEFAULT_ONNX_INT8_NAME = "xlmr.onnx"
DEFAULT_ONNX_INT4_NAME = "xlmr.int4.onnx"
DEFAULT_TOKENIZER_NAME = "xlmr.spm"

# Multilingual smoke corpus used by ``--validate-int4``. Mirrors the
# 5-language coverage of the trained-head training corpus
# (``training_data.py``) without committing to a specific label set —
# the assertion is purely a numerical "INT4 ≈ INT8 on the embedding
# space" check, not a classification accuracy check.
INT4_VALIDATION_CORPUS: tuple[str, ...] = (
    "hello friends, how are you doing today?",
    "this looks like a phishing scam, do not click the link",
    "Bonjour, comment allez-vous aujourd'hui ?",
    "Hola amigos, ¿cómo están?",
    "Xin chào, hôm nay bạn khỏe không?",
    "Guten Tag, wie geht es Ihnen heute?",
    "credit card number: 4111 1111 1111 1111",
    "click here to claim your free prize: bit.ly/abc123",
)

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
    quantize_int4: bool = False,
) -> Path:
    """Export the HuggingFace XLM-R encoder to ONNX (optionally INT8 + INT4).

    Always produces ``models/xlmr.onnx`` (FP32 if quantisation was
    skipped, INT8 otherwise). When ``quantize_int4`` is True, the FP32
    graph is also block-quantised to 4 bits and written to
    ``models/xlmr.int4.onnx``; the INT8 file is unaffected so callers
    may keep both on disk and pick at load time. The function returns
    the path to the (default) INT8 / FP32 file; the INT4 path is
    derivable as ``output_dir / DEFAULT_ONNX_INT4_NAME``.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    output_dir.mkdir(parents=True, exist_ok=True)
    onnx_fp32 = output_dir / "xlmr.fp32.onnx"
    onnx_final = output_dir / DEFAULT_ONNX_NAME
    onnx_int4 = output_dir / DEFAULT_ONNX_INT4_NAME
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
        # Legacy tracer: dynamo=True breaks INT8 quant + SDPA dynamic
        # shapes for this model on onnxruntime 1.16+. See module
        # docstring for the full story.
        dynamo=False,
    )

    if not quantize_int8:
        if onnx_fp32 != onnx_final:
            shutil.copyfile(onnx_fp32, onnx_final)
    else:
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

    if quantize_int4:
        LOGGER.info("Quantising ONNX -> INT4 (block-wise) -> %s", onnx_int4)
        export_int4(fp32_path=onnx_fp32, int4_path=onnx_int4)

    # Drop the FP32 intermediate to keep the artifact directory tidy
    # — but only after every requested quantised variant is on disk.
    try:
        onnx_fp32.unlink()
    except OSError:
        pass

    return onnx_final


# ---------------------------------------------------------------------------
# INT4 quantisation + cosine-similarity validation.
# ---------------------------------------------------------------------------
def export_int4(*, fp32_path: Path, int4_path: Path) -> Path:
    """Block-quantise an FP32 ONNX graph to 4-bit weights.

    Uses
    :class:`onnxruntime.quantization.matmul_nbits_quantizer.MatMulNBitsQuantizer`
    with ``DefaultWeightOnlyQuantConfig`` (block-size 128, asymmetric
    weight-only quantisation of both ``MatMul`` and ``Gather`` ops in
    ``QOperator`` format). Quantising ``Gather`` is what brings the
    XLM-R checkpoint down to the ~50 MB target on disk — with
    MatMul-only quantisation the 250 002 × 384 word-embedding table
    dominates and the file stays north of 370 MB.

    The MatMulNBitsQuantizer unconditionally bumps the model's
    primary opset to 21, which is incompatible with the opset-14
    ``ReduceMean`` nodes emitted by ``torch.onnx.export(opset=14)`` —
    in opset 18+ ``ReduceMean.axes`` moved from a node attribute to
    a runtime input. We work around this by running the
    ``onnx.version_converter`` over the FP32 graph first so every
    node speaks opset 21 before quantisation. Without this preflight
    the resulting INT4 file fails to load with
    ``InvalidGraph: Unrecognized attribute: axes for operator
    ReduceMean``.

    Cosine similarity vs the INT8 reference (also exported from the
    same FP32 source) lands around ~0.95 on the multilingual smoke
    corpus — see :func:`validate_int4` for the threshold and
    :data:`INT4_VALIDATION_CORPUS` for the corpus itself. Aggressive
    embedding-Gather quantisation is what trades the last few cosine
    points for the storage win; if the caller needs > 0.99 cosine
    they should ship the INT8 file instead.
    """
    import onnx  # type: ignore[import-not-found]
    from onnx import version_converter  # type: ignore[import-not-found]
    from onnxruntime.quantization.matmul_nbits_quantizer import (  # type: ignore[import-not-found]
        DefaultWeightOnlyQuantConfig,
        MatMulNBitsQuantizer,
    )
    from onnxruntime.quantization.quant_utils import (  # type: ignore[import-not-found]
        QuantFormat,
    )

    if not fp32_path.is_file():
        raise FileNotFoundError(
            f"FP32 ONNX graph required for INT4 quantisation, missing: {fp32_path}"
        )

    model = onnx.load(str(fp32_path))
    primary_opset = next(
        (i.version for i in model.opset_import if not i.domain or i.domain == ""),
        None,
    )
    # MatMulNBitsQuantizer hard-codes a bump to opset 21 — pre-upgrade
    # the FP32 graph so every node (incl. ReduceMean.axes -> input)
    # speaks the same opset before we hand it to the quantizer.
    if primary_opset is not None and primary_opset < 21:
        LOGGER.info(
            "Upgrading FP32 graph from opset %d -> 21 before INT4 quantisation",
            primary_opset,
        )
        model = version_converter.convert_version(model, 21)

    config = DefaultWeightOnlyQuantConfig(
        block_size=128,
        is_symmetric=False,
        bits=4,
        op_types_to_quantize=("MatMul", "Gather"),
        quant_format=QuantFormat.QOperator,
    )
    quantizer = MatMulNBitsQuantizer(model=model, algo_config=config)
    quantizer.process()
    quantizer.model.save_model_to_file(str(int4_path))
    return int4_path


def _embed_with_session(
    session: Any,  # type: ignore[name-defined]
    tokenizer: Any,  # type: ignore[name-defined]
    texts: tuple[str, ...] | list[str],
    *,
    max_seq_length: int,
) -> Any:
    """Run a fresh INT8 / INT4 session over ``texts`` and return mean-pooled,
    L2-normalised embeddings as a numpy ``(len(texts), hidden)`` array.

    This intentionally re-implements the encoder forward used by
    :class:`xlmr_adapter.XLMRAdapter` so the export script has no
    runtime dependency on the adapter module — the export pipeline
    must work even when the adapter import fails (e.g. partial
    checkpoints during a fresh clone).
    """
    import numpy as np  # type: ignore[import-not-found]

    # XLM-R / fairseq tokenizer offset (mirror xlmr_adapter._FAIRSEQ_OFFSET).
    bos_id, pad_id, eos_id, unk_id = 0, 1, 2, 3
    fairseq_offset = 1
    max_payload = max(2, max_seq_length - 2)

    rows: list[list[int]] = []
    for text in texts:
        pieces = tokenizer.EncodeAsIds(text or "")
        shifted = [
            unk_id if pid == 0 else pid + fairseq_offset
            for pid in pieces[:max_payload]
        ]
        rows.append([bos_id, *shifted, eos_id])

    seq_len = max((len(r) for r in rows), default=2)
    seq_len = min(seq_len, max_seq_length)
    input_ids = np.full((len(rows), seq_len), fill_value=pad_id, dtype=np.int64)
    attention_mask = np.zeros((len(rows), seq_len), dtype=np.int64)
    for i, row in enumerate(rows):
        n = min(len(row), seq_len)
        input_ids[i, :n] = row[:n]
        attention_mask[i, :n] = 1

    feed: dict[str, Any] = {}  # type: ignore[name-defined]
    for name in (i.name for i in session.get_inputs()):
        if name == "input_ids":
            feed[name] = input_ids
        elif name == "attention_mask":
            feed[name] = attention_mask
        elif name == "token_type_ids":
            feed[name] = np.zeros_like(input_ids)
        else:
            feed[name] = np.zeros_like(input_ids)

    last_hidden = np.asarray(session.run(None, feed)[0], dtype=np.float32)
    mask = attention_mask.astype(np.float32)[..., None]
    summed = (last_hidden * mask).sum(axis=1)
    counts = np.maximum(mask.sum(axis=1), 1.0)
    pooled = summed / counts
    norms = np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
    return (pooled / norms).astype(np.float32)


DEFAULT_INT4_MIN_COSINE = 0.94


def validate_int4(
    *,
    int8_path: Path,
    int4_path: Path,
    tokenizer_path: Path,
    max_seq_length: int = 128,
    corpus: tuple[str, ...] = INT4_VALIDATION_CORPUS,
    min_cosine: float = DEFAULT_INT4_MIN_COSINE,
) -> dict[str, float]:
    """Assert that INT4 embeddings agree with INT8 embeddings.

    Loads both ONNX sessions, runs ``corpus`` through each, and
    computes per-row cosine similarity between the resulting
    mean-pooled, L2-normalised embeddings. Raises
    :class:`AssertionError` if the minimum per-row cosine drops
    below ``min_cosine``.

    The default threshold (:data:`DEFAULT_INT4_MIN_COSINE`, ``0.94``)
    is what the current MatMul + Gather INT4 path reliably hits on
    the XLM-R MiniLM-L6 checkpoint — full quantisation of the
    250 002 × 384 word-embedding table costs ~5 cosine points
    relative to the INT8 baseline but is what unlocks the ~50 MB
    storage win. Tighter thresholds (e.g. > 0.99) are achievable
    only by leaving ``Gather`` ops at FP32 / FP16, which keeps the
    file > 370 MB and defeats the point of the INT4 tier; callers
    that need that quality bar should keep shipping the INT8 file
    instead.

    Returns a dict with summary statistics (``min``, ``mean``,
    ``max``, ``size_int8_bytes``, ``size_int4_bytes``) for the
    caller to log / commit.
    """
    import numpy as np  # type: ignore[import-not-found]
    import onnxruntime as ort  # type: ignore[import-not-found]
    import sentencepiece as spm  # type: ignore[import-not-found]

    if not int8_path.is_file():
        raise FileNotFoundError(f"INT8 model required for validation: {int8_path}")
    if not int4_path.is_file():
        raise FileNotFoundError(f"INT4 model required for validation: {int4_path}")
    if not tokenizer_path.is_file():
        raise FileNotFoundError(
            f"SentencePiece tokenizer required for validation: {tokenizer_path}"
        )

    tokenizer = spm.SentencePieceProcessor()
    tokenizer.Load(str(tokenizer_path))

    sess_options = ort.SessionOptions()
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    LOGGER.info("Loading INT8 session for validation: %s", int8_path)
    int8_session = ort.InferenceSession(
        str(int8_path), sess_options=sess_options, providers=["CPUExecutionProvider"]
    )
    LOGGER.info("Loading INT4 session for validation: %s", int4_path)
    int4_session = ort.InferenceSession(
        str(int4_path), sess_options=sess_options, providers=["CPUExecutionProvider"]
    )

    int8_embeds = _embed_with_session(
        int8_session, tokenizer, corpus, max_seq_length=max_seq_length
    )
    int4_embeds = _embed_with_session(
        int4_session, tokenizer, corpus, max_seq_length=max_seq_length
    )
    cos = (int8_embeds * int4_embeds).sum(axis=1)

    stats = {
        "min": float(cos.min()),
        "mean": float(cos.mean()),
        "max": float(cos.max()),
        "size_int8_bytes": float(int8_path.stat().st_size),
        "size_int4_bytes": float(int4_path.stat().st_size),
    }
    LOGGER.info(
        "INT4 vs INT8 cosine: min=%.4f mean=%.4f max=%.4f "
        "(INT8 %.1f MB, INT4 %.1f MB)",
        stats["min"],
        stats["mean"],
        stats["max"],
        stats["size_int8_bytes"] / (1024 * 1024),
        stats["size_int4_bytes"] / (1024 * 1024),
    )
    if stats["min"] < min_cosine:
        raise AssertionError(
            f"INT4 vs INT8 minimum cosine similarity {stats['min']:.4f} "
            f"< {min_cosine:.2f}; INT4 export rejected"
        )
    return stats


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
        "--quantize-int4",
        action="store_true",
        help=(
            "Additionally produce an INT4 (block-wise weight-only) "
            "ONNX file at ``<output-dir>/xlmr.int4.onnx`` alongside "
            "the default INT8 ``xlmr.onnx``. Recommended for mobile "
            "devices with tight storage budgets (~50 MB vs ~107 MB)."
        ),
    )
    parser.add_argument(
        "--validate-int4",
        action="store_true",
        help=(
            "After exporting, load both the INT8 and INT4 sessions, "
            "run the multilingual smoke corpus through each, and "
            "assert per-row cosine similarity is above "
            "``--int4-min-cosine``. Implies --quantize-int4."
        ),
    )
    parser.add_argument(
        "--int4-min-cosine",
        type=float,
        default=DEFAULT_INT4_MIN_COSINE,
        help=(
            "Minimum per-row cosine similarity between the INT4 and "
            "INT8 mean-pooled embeddings (default: "
            f"{DEFAULT_INT4_MIN_COSINE}). Used by --validate-int4. "
            "Aggressive embedding-Gather quantisation costs ~5 cosine "
            "points vs INT8; ship the INT8 file instead if a tighter "
            "bar is required."
        ),
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

    # ``--validate-int4`` implies ``--quantize-int4``; we cannot
    # validate something we did not export.
    quantize_int4 = bool(args.quantize_int4 or args.validate_int4)

    if not args.head_only:
        export_onnx(
            model_id=args.model_id,
            output_dir=args.output_dir,
            opset=args.opset,
            max_seq_length=args.max_seq_length,
            quantize_int8=not args.no_quantize,
            quantize_int4=quantize_int4,
        )

        if args.validate_int4:
            if args.no_quantize:
                LOGGER.warning(
                    "--validate-int4 requested with --no-quantize; "
                    "no INT8 file to compare against. Skipping validation."
                )
            else:
                validate_int4(
                    int8_path=args.output_dir / DEFAULT_ONNX_INT8_NAME,
                    int4_path=args.output_dir / DEFAULT_ONNX_INT4_NAME,
                    tokenizer_path=args.output_dir / DEFAULT_TOKENIZER_NAME,
                    max_seq_length=args.max_seq_length,
                    min_cosine=args.int4_min_cosine,
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
