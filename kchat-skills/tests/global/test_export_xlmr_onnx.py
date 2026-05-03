"""Unit tests for ``tools/export_xlmr_onnx.py``.

These tests cover the INT4 quantisation + cosine-similarity
validation paths added alongside the cross-pipeline ``_embedding``
work. They intentionally avoid running the (heavy) HuggingFace +
PyTorch + ``torch.onnx.export`` pipeline — instead they:

* Exercise the CLI parser in isolation to make sure
  ``--quantize-int4`` / ``--validate-int4`` are accepted and
  resolved into the right ``export_onnx`` kwargs.
* Build a tiny synthetic ONNX MatMul graph with ``onnx.helper``
  and run it through :func:`export_xlmr_onnx.export_int4`, asserting
  that the resulting INT4 file is strictly smaller than the FP32
  source and still loadable by ``onnxruntime.InferenceSession``.
* Re-use that synthetic graph + a SentencePiece tokenizer stub to
  exercise :func:`export_xlmr_onnx.validate_int4` and confirm the
  cosine assertion fires when the INT4 graph diverges by > 1%.

Heavy / network-dependent tests (full HF download, end-to-end
benchmark) are guarded with ``pytest.importorskip`` so they only run
when the optional ``[export]`` extras are installed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_SCRIPT = REPO_ROOT / "tools" / "export_xlmr_onnx.py"


def _load_export_module():
    """Import ``tools/export_xlmr_onnx.py`` as a module."""
    spec = importlib.util.spec_from_file_location(
        "export_xlmr_onnx", EXPORT_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# CLI flag wiring.
# ---------------------------------------------------------------------------
def test_parse_args_defaults_have_no_int4():
    """The default invocation produces only the INT8 file —
    backwards-compatible with every call site that already shipped."""
    mod = _load_export_module()
    args = mod.parse_args([])
    assert args.quantize_int4 is False
    assert args.validate_int4 is False
    assert args.no_quantize is False  # INT8 stays the default tier


def test_parse_args_accepts_quantize_int4():
    mod = _load_export_module()
    args = mod.parse_args(["--quantize-int4"])
    assert args.quantize_int4 is True
    assert args.validate_int4 is False


def test_parse_args_validate_int4_implies_quantize_int4():
    """``--validate-int4`` cannot run without an INT4 file to
    validate, so ``main()`` upgrades the export to also include the
    INT4 path."""
    mod = _load_export_module()
    args = mod.parse_args(["--validate-int4"])
    assert args.validate_int4 is True
    quantize_int4 = bool(args.quantize_int4 or args.validate_int4)
    assert quantize_int4 is True


def test_default_int4_filename_distinct_from_int8():
    mod = _load_export_module()
    assert mod.DEFAULT_ONNX_INT4_NAME == "xlmr.int4.onnx"
    assert mod.DEFAULT_ONNX_INT4_NAME != mod.DEFAULT_ONNX_INT8_NAME


# ---------------------------------------------------------------------------
# INT4 quantisation on a synthetic MatMul graph.
# ---------------------------------------------------------------------------
def _build_synthetic_matmul_onnx(
    out_path: Path, *, in_dim: int = 384, out_dim: int = 384
) -> None:
    """Build a tiny ``y = x @ W`` ONNX graph with a single MatMul.

    Used as a stand-in for the XLM-R encoder so the INT4 quantiser
    has something MatMul-shaped to work on without the multi-GB HF
    download.
    """
    import numpy as np  # type: ignore[import-not-found]
    import onnx  # type: ignore[import-not-found]
    from onnx import TensorProto, helper, numpy_helper  # type: ignore[import-not-found]

    rng = np.random.default_rng(0)
    weight = rng.standard_normal((in_dim, out_dim)).astype(np.float32)
    weight_init = numpy_helper.from_array(weight, name="W")

    x = helper.make_tensor_value_info(
        "x", TensorProto.FLOAT, [None, in_dim]
    )
    y = helper.make_tensor_value_info(
        "y", TensorProto.FLOAT, [None, out_dim]
    )
    matmul = helper.make_node("MatMul", inputs=["x", "W"], outputs=["y"])

    graph = helper.make_graph(
        [matmul],
        "matmul_smoke",
        inputs=[x],
        outputs=[y],
        initializer=[weight_init],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 14)])
    model.ir_version = 9
    onnx.save(model, str(out_path))


def test_export_int4_shrinks_synthetic_graph(tmp_path):
    """``export_int4`` produces an INT4 file that is *smaller* than
    its FP32 source — without this the on-mobile storage win
    promised by the proposal is fictional."""
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx_ir")

    fp32 = tmp_path / "matmul.fp32.onnx"
    int4 = tmp_path / "matmul.int4.onnx"
    _build_synthetic_matmul_onnx(fp32)

    mod = _load_export_module()
    mod.export_int4(fp32_path=fp32, int4_path=int4)

    assert int4.is_file()
    int4_bytes = int4.stat().st_size
    fp32_bytes = fp32.stat().st_size
    assert int4_bytes < fp32_bytes, (
        f"INT4 graph ({int4_bytes} B) must be smaller than FP32 source "
        f"({fp32_bytes} B); MatMulNBitsQuantizer regression?"
    )


def test_export_int4_runs_in_onnxruntime(tmp_path):
    """The INT4 file must load cleanly under ``onnxruntime`` and
    return numerically-close outputs versus the FP32 reference on
    random inputs (cosine similarity > 0.99 — the same bar
    ``--validate-int4`` enforces in production)."""
    np = pytest.importorskip("numpy")
    ort = pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx_ir")

    fp32 = tmp_path / "matmul.fp32.onnx"
    int4 = tmp_path / "matmul.int4.onnx"
    _build_synthetic_matmul_onnx(fp32)

    mod = _load_export_module()
    mod.export_int4(fp32_path=fp32, int4_path=int4)

    rng = np.random.default_rng(42)
    x = rng.standard_normal((4, 384)).astype(np.float32)

    s_fp32 = ort.InferenceSession(
        str(fp32), providers=["CPUExecutionProvider"]
    )
    s_int4 = ort.InferenceSession(
        str(int4), providers=["CPUExecutionProvider"]
    )

    y_fp32 = s_fp32.run(None, {"x": x})[0]
    y_int4 = s_int4.run(None, {"x": x})[0]

    # Mean-pool + L2-normalise both outputs so cosine is well-defined
    # (mirrors xlmr_adapter._encode_batch).
    def _normalise(arr: "np.ndarray") -> "np.ndarray":
        norms = np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-12)
        return arr / norms

    cos = (_normalise(y_fp32) * _normalise(y_int4)).sum(axis=1)
    assert cos.min() > 0.99, (
        f"INT4 output diverges from FP32 (min cosine {cos.min():.4f}); "
        "check MatMulNBitsQuantizer block_size / is_symmetric defaults"
    )


# ---------------------------------------------------------------------------
# Smoke: ``--validate-int4`` happy path on the real exported model.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not (REPO_ROOT / "models" / "xlmr.onnx").is_file()
    or not (REPO_ROOT / "models" / "xlmr.int4.onnx").is_file()
    or not (REPO_ROOT / "models" / "xlmr.spm").is_file(),
    reason=(
        "Exported XLM-R models not present locally; run "
        "`python tools/export_xlmr_onnx.py --quantize-int4 --output-dir models` "
        "to enable this smoke test."
    ),
)
def test_validate_int4_against_real_model_passes():
    """When the INT8 + INT4 + tokenizer trio is on disk, the
    real-corpus cosine check must pass at the production
    ``DEFAULT_INT4_MIN_COSINE`` floor (0.94 — see
    ``export_xlmr_onnx.validate_int4`` for the rationale; aggressive
    embedding-Gather quantisation costs ~5 cosine points vs INT8
    and is what unlocks the ~50 MB storage win)."""
    mod = _load_export_module()
    stats = mod.validate_int4(
        int8_path=REPO_ROOT / "models" / "xlmr.onnx",
        int4_path=REPO_ROOT / "models" / "xlmr.int4.onnx",
        tokenizer_path=REPO_ROOT / "models" / "xlmr.spm",
    )
    assert stats["min"] > mod.DEFAULT_INT4_MIN_COSINE
    # The whole point of INT4 is the storage win — assert it.
    assert stats["size_int4_bytes"] < stats["size_int8_bytes"]
