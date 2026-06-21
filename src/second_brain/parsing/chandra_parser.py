"""Handwritten / scanned PDF parser using Chandra 2 (Datalab) on MLX.

Chandra 2 is a Qwen3.5 hybrid (linear-attention / SSM) OCR model. On Apple
Silicon, transformers + MPS falls back to a slow pure-torch path for the
hybrid layers (~275s/page). Running it through MLX with a quantized model is
~9x faster (~31s/page at 4-bit) with no quality loss after adjudication.

The model is converted to MLX from the official ``datalab-to/chandra-ocr-2``
weights on first use (no separate vLLM server required).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz

from second_brain.parsing.provider import (
    DocumentParser,
    PageParseError,
    ParseBlock,
    ParseLane,
    ParseResult,
)

logger = logging.getLogger(__name__)

# Markdown separator inserted between assembled pages. Must round-trip
# through cache hits so reused pages match a fresh full parse.
PAGE_SEPARATOR = "\n\n---\n\n"

# Official source weights; converted to a local MLX model on first use.
HF_MODEL = "datalab-to/chandra-ocr-2"

# The model ends its turn with this token; mlx-vlm must be told to stop on
# it or generation loops and repeats the page.
STOP_TOKEN = "<|im_end|>"

MAX_OUTPUT_TOKENS = 6144


def mlx_model_dir(precision: str = "4bit") -> Path:
    """Local path of the converted MLX model for a given precision."""
    return Path.home() / ".cache" / "second-brain" / f"chandra-ocr-2-mlx-{precision}"


def ensure_mlx_model(precision: str = "4bit") -> Path:
    """Convert Chandra 2 to a quantized MLX model if not already present.

    Reuses the cached HuggingFace weights when available; otherwise the
    convert step downloads them. Quantizing keeps the vision tower at higher
    precision automatically.

    Parameters
    ----------
    precision: str
        ``"4bit"`` (default) or ``"8bit"``.

    Returns
    -------
    Path
        Directory of the ready-to-load MLX model.
    """
    path = mlx_model_dir(precision)
    if (path / "config.json").exists():
        return path
    bits = "8" if precision == "8bit" else "4"
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Converting Chandra 2 to MLX %s (one-time, a few minutes)...", precision)
    subprocess.run(
        [
            sys.executable, "-m", "mlx_vlm", "convert",
            "--hf-path", HF_MODEL,
            "--mlx-path", str(path),
            "-q", "--q-bits", bits, "--q-group-size", "64",
            "--trust-remote-code",
        ],
        check=True,
    )
    return path


@dataclass(frozen=True)
class RenderedPage:
    """A single PDF page rasterized for both hashing and OCR."""
    page_number: int  # 1-indexed
    png_bytes: bytes


def assemble_chandra_result(
    source: Path,
    rendered: list[RenderedPage],
    page_markdowns: list[str],
) -> ParseResult:
    """Combine per-page OCR markdown into a document-level :class:`ParseResult`.

    Parameters
    ----------
    source: Path
        Original PDF, recorded in result metadata.
    rendered: list[RenderedPage]
        Pages in document order, for numbering.
    page_markdowns: list[str]
        OCR markdown aligned 1:1 with ``rendered``.

    Returns
    -------
    ParseResult
        Stitched markdown plus one ``ParseBlock`` per page.

    Raises
    ------
    ValueError
        If ``rendered`` and ``page_markdowns`` differ in length.
    """
    if len(rendered) != len(page_markdowns):
        raise ValueError(
            f"rendered/page_markdowns length mismatch: {len(rendered)} vs {len(page_markdowns)}"
        )

    blocks = [
        ParseBlock(
            content=md,
            block_type="text",
            page_number=page.page_number,
            reading_order=page.page_number - 1,
        )
        for page, md in zip(rendered, page_markdowns, strict=True)
    ]
    return ParseResult(
        markdown=PAGE_SEPARATOR.join(page_markdowns),
        blocks=blocks,
        metadata={
            "source": str(source),
            "parse_lane": ParseLane.CHANDRA.value,
            "pages": len(rendered),
        },
    )


class ChandraParser(DocumentParser):
    """Parses handwritten/scanned PDFs via Chandra 2 on the MLX backend."""

    def __init__(self, precision: str = "4bit") -> None:
        self._precision = precision
        self._model = None
        self._processor = None
        self._mlx_config = None

    def _ensure_loaded(self) -> None:
        """Lazy-load the MLX model, converting it on first use."""
        if self._model is not None:
            return
        from mlx_vlm import load
        from mlx_vlm.utils import load_config

        path = ensure_mlx_model(self._precision)
        self._model, self._processor = load(str(path), trust_remote_code=True)
        self._mlx_config = load_config(str(path), trust_remote_code=True)

    @staticmethod
    def render_pdf_pages(file_path: Path) -> list[RenderedPage]:
        """Rasterize every page of a PDF to PNG bytes at ``RENDER_DPI``.

        Returning raw PNG bytes lets the caller SHA-256 each page for the
        page cache without re-encoding.

        Parameters
        ----------
        file_path: Path
            PDF to rasterize.

        Returns
        -------
        list[RenderedPage]
            One entry per page in document order.
        """
        from second_brain.parsing import RENDER_DPI

        doc = fitz.open(str(file_path))
        pages: list[RenderedPage] = []
        try:
            for idx, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=RENDER_DPI)
                pages.append(RenderedPage(page_number=idx, png_bytes=pix.tobytes("png")))
        finally:
            doc.close()
        return pages

    def parse_pages(self, pages: list[RenderedPage]) -> list[str]:
        """Run Chandra on a (possibly partial) set of pre-rendered pages.

        Accepts a subset of a document so cached pages can be skipped.
        Output markdown mirrors the input order.

        Parameters
        ----------
        pages: list[RenderedPage]
            Pages to OCR. May be a subset of a larger document.

        Returns
        -------
        list[str]
            One markdown string per input page, in the same order.

        Raises
        ------
        PageParseError
            If any page fails to OCR. Fails fast so the document is marked
            failed and retried rather than silently losing a page.
        """
        if not pages:
            return []

        from chandra.output import parse_markdown
        from chandra.prompts import PROMPT_MAPPING
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        self._ensure_loaded()
        prompt = apply_chat_template(
            self._processor, self._mlx_config, PROMPT_MAPPING["ocr_layout"], num_images=1
        )

        results: list[str] = []
        for page in pages:
            with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                tmp.write(page.png_bytes)
                tmp.flush()
                try:
                    out = generate(
                        self._model,
                        self._processor,
                        prompt,
                        image=tmp.name,
                        max_tokens=MAX_OUTPUT_TOKENS,
                        eos_tokens=[STOP_TOKEN],
                        verbose=False,
                    )
                    raw = out.text if hasattr(out, "text") else str(out)
                except Exception as e:
                    logger.exception("Chandra MLX failed on page %d", page.page_number)
                    raise PageParseError(
                        f"Chandra failed on page {page.page_number}"
                    ) from e
                results.append(parse_markdown(raw))
        return results

    async def parse(self, file_path: str) -> ParseResult:
        """OCR a scanned/handwritten PDF page-by-page via Chandra 2.

        Parameters
        ----------
        file_path: str
            Absolute path to the PDF file.

        Returns
        -------
        ParseResult
            Stitched markdown and one block per page.
        """
        source = Path(file_path)
        rendered = self.render_pdf_pages(source)
        page_markdowns = self.parse_pages(rendered)
        return assemble_chandra_result(source, rendered, page_markdowns)

    async def health_check(self) -> bool:
        """Verify the MLX model can be loaded (converting it if needed).

        Returns
        -------
        bool
            True if the model loads successfully.
        """
        try:
            self._ensure_loaded()
            return True
        except Exception:
            logger.exception("Chandra MLX health check failed")
            return False
