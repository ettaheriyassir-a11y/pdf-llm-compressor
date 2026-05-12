import fitz  # PyMuPDF
import tiktoken
import re
import platform
from typing import List, Dict, Any, Tuple, Callable, Optional, Union
from collections import Counter, defaultdict

# ── Optional OCR imports ───────────────────────────────────────
try:
    import pytesseract
    from PIL import Image
    import io as _io
    # Gap 2: On Windows point pytesseract at the default install path
    if platform.system() == "Windows":
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# ──────────────────────────────────────────────────────────────
# Tokenizer helpers
# ──────────────────────────────────────────────────────────────

_TOKENIZER = None

def get_tokenizer():
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = tiktoken.get_encoding("cl100k_base")
    return _TOKENIZER

def count_tokens(text: str) -> int:
    return len(get_tokenizer().encode(text))

# ──────────────────────────────────────────────────────────────
# Text utilities
# ──────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """Normalise whitespace and strip. Unwraps single line breaks to save tokens."""
    # Replace single newlines with a space (unwrap intra-paragraph lines)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    # Normalize spaces
    text = re.sub(r'[ \t]+', ' ', text)
    # Collapse multiple newlines into one
    text = re.sub(r'\n{2,}', '\n', text)
    return text.strip()

def is_heading(text: str, font_size: float, median_font: float) -> bool:
    """
    A block is a heading if:
      - Its dominant font size is noticeably larger than the page median, OR
      - It is a short line (≤ 60 chars), title cased, and doesn't end in punctuation.
    """
    if font_size > median_font * 1.15:
        return True
    if len(text) <= 60 and text.istitle() and text[-1] not in '.!?,;:' and not re.match(r'^[\-•\*\d]', text):
        return True
    return False

def is_list_item(text: str) -> bool:
    return bool(re.match(r'^(\s*[\-•\*]\s+|\s*\d+[\.\)]\s+)', text))

# ──────────────────────────────────────────────────────────────
# Gap 2: OCR helper
# ──────────────────────────────────────────────────────────────

def ocr_page(page: fitz.Page) -> str:
    """Rasterize a page at 200 DPI and extract text via Tesseract."""
    if not OCR_AVAILABLE:
        return ""
    try:
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        img = Image.open(_io.BytesIO(img_bytes))
        return pytesseract.image_to_string(img)
    except Exception:
        return ""

# ──────────────────────────────────────────────────────────────
# Main processing pipeline
# ──────────────────────────────────────────────────────────────

def process_pdf(
    file_bytes: bytes,
    use_dict: bool = True,
    dedup_headers: bool = True,
    minify: bool = True,
    chunk_token_limit: int = 800,           # Gap 4: configurable
    progress_cb: Optional[Callable[[str], None]] = None,  # Gap 5: SSE hook
) -> Tuple[str, int, int, List[Dict[str, Any]]]:
    """
    Convert a PDF to a compressed, token-efficient XML document.

    Returns:
        xml_content        – the full compressed XML string
        original_tokens    – token count of raw extracted text
        compressed_tokens  – token count of the XML output
        chunks             – list of dicts with text and exact tokens suitable for RAG
    """
    def emit(stage: str):
        if progress_cb:
            progress_cb(stage)

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    num_pages = len(doc)
    emit("reading_pdf")

    original_text_parts: List[str] = []

    # ── Pass 1: extract blocks with font metadata ──────────────
    all_blocks: List[Dict[str, Any]] = []
    text_page_map: Dict[str, set] = defaultdict(set)
    text_counts: Counter = Counter()

    for page_num in range(num_pages):
        emit(f"extracting_page:{page_num + 1}:{num_pages}")
        page = doc[page_num]
        raw_page_text = page.get_text()

        if len(raw_page_text.strip()) < 20:
            emit(f"ocr_processing:{page_num + 1}:{num_pages}")
            ocr_text = ocr_page(page)
            if ocr_text.strip():
                original_text_parts.append(ocr_text)
                cleaned = clean_text(ocr_text)
                if cleaned:
                    all_blocks.append({
                        "page": page_num + 1,
                        "text": cleaned,
                        "is_heading": False,
                        "is_list": False,
                    })
                    if len(cleaned) > 10:
                        text_page_map[cleaned].add(page_num)
                        text_counts[cleaned] += 1
            else:
                # No text at all – note it but skip
                original_text_parts.append("")
            continue  # done with this page

        original_text_parts.append(raw_page_text)

        # ── Gap 1: build block_font_map (bbox → dominant font) ─
        font_sizes: List[float] = []
        block_font_map: Dict[tuple, float] = {}

        raw_blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for rb in raw_blocks:
            if rb.get("type") != 0:
                continue
            sizes_in_block: List[float] = []
            for line in rb.get("lines", []):
                for span in line.get("spans", []):
                    sz = span.get("size")
                    if sz:
                        font_sizes.append(sz)
                        sizes_in_block.append(sz)
            if sizes_in_block:
                # Max font wins — catches headings even when mixed with smaller text
                key = tuple(round(x, 1) for x in rb["bbox"])
                block_font_map[key] = max(sizes_in_block)

        median_font = sorted(font_sizes)[len(font_sizes) // 2] if font_sizes else 12.0

        # Process regular "blocks" output (simpler structure)
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: (round(b[1] / 10), b[0]))  # top-to-bottom, left-right

        for b in blocks:
            if b[6] != 0:   # skip image blocks
                continue
            text = clean_text(b[4])
            if not text:
                continue

            # Gap 1: use actual block font size from map
            bbox_key = tuple(round(x, 1) for x in b[:4])
            block_font = block_font_map.get(bbox_key, median_font)

            block_is_heading = is_heading(text, block_font, median_font)
            block_is_list = is_list_item(text)

            all_blocks.append({
                "page": page_num + 1,
                "text": text,
                "is_heading": block_is_heading and not block_is_list,
                "is_list": block_is_list,
            })

            if len(text) > 10:
                text_page_map[text].add(page_num)
                text_counts[text] += 1

    # ── Identify repeated headers / footers ───────────────────
    headers_footers: set = set()
    if dedup_headers and num_pages > 1:
        for text, pages in text_page_map.items():
            if len(pages) >= max(2, num_pages * 0.3):
                headers_footers.add(text)

    # ── Build compression dictionary ──────────────────────────
    emit("building_dict")
    dictionary: Dict[str, int] = {}
    if use_dict:
        # Token costs (cl100k_base, empirically measured):
        #   <r i="N"/>  = 5 tokens  (reference tag, constant for N < 1000)
        #   <w i="N">text</w>  ~ count_tokens(text) + 8  (entry overhead)
        #
        # For phrase with t_tok tokens appearing c times, dictionary helps when:
        #   c * t_tok > (t_tok + ENTRY_OH) + c * REF_TOK
        #   c > (t_tok + ENTRY_OH) / (t_tok - REF_TOK)   [only valid if t_tok > REF_TOK]
        REF_TOK  = 5
        ENTRY_OH = 8

        dict_counter = 1
        candidates = sorted(
            [(t, c) for t, c in text_counts.items()
             if t not in headers_footers and c > 1 and len(t) > 30],
            key=lambda x: x[1] * len(x[0]),
            reverse=True,
        )
        for text, count in candidates:
            t_tok = count_tokens(text)
            if t_tok <= REF_TOK:
                continue   # reference as long as phrase – skip
            min_needed = (t_tok + ENTRY_OH) / (t_tok - REF_TOK)
            if count <= min_needed:
                continue   # would increase token count – skip
            dictionary[text] = dict_counter
            dict_counter += 1

    # ── Pass 2: build XML ────────────────────────────────────
    emit("generating_xml")
    nl  = "" if minify else "\n"
    ind = "" if minify else "  "

    xml_parts: List[str] = ["<d>"]

    if use_dict and dictionary:
        xml_parts.append(f"{nl}{ind}<dict>")
        for text, idx in dictionary.items():
            safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml_parts.append(f'{nl}{ind}{ind}<w i="{idx}">{safe}</w>')
        xml_parts.append(f"{nl}{ind}</dict>")

    current_page  = -1
    last_was_body = False   # tracks whether to insert a body-text separator

    for b in all_blocks:
        page_num = b["page"]
        text = b["text"]

        if dedup_headers and text in headers_footers:
            continue

        if page_num != current_page:
            if current_page != -1:
                xml_parts.append(f"{nl}{ind}</pg>")
            xml_parts.append(f'{nl}{ind}<pg n="{page_num}">')
            current_page  = page_num
            last_was_body = False

        if use_dict and text in dictionary:
            tag_content = f'<r i="{dictionary[text]}"/>'
        else:
            safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            tag_content = safe

        if b["is_heading"]:
            # Always tag headings — they carry semantic weight
            xml_parts.append(f'{nl}{ind}{ind}<h>{tag_content}</h>')
            last_was_body = False
        elif b["is_list"]:
            # Always tag list items
            xml_parts.append(f'{nl}{ind}{ind}<li>{tag_content}</li>')
            last_was_body = False
        else:
            # Body text: skip the <t> wrapper in minified mode to avoid
            # paying 2 tokens ("<t>" + "</t>") per block.
            # In pretty-print mode keep <t> for human readability.
            if minify:
                sep = "\n" if last_was_body else ""
                xml_parts.append(f"{sep}{tag_content}")
            else:
                xml_parts.append(f'{nl}{ind}{ind}<t>{tag_content}</t>')
            last_was_body = True

    if current_page != -1:
        xml_parts.append(f"{nl}{ind}</pg>")

    xml_parts.append(f"{nl}</d>")
    xml_content = "".join(xml_parts)

    # ── Pass 3: token-aware RAG chunking ──────────────────────
    emit("chunking")
    chunks: List[Dict[str, Any]] = []
    dict_preamble = ""
    page_segments: List[str] = []

    in_dict = False
    current_seg: List[str] = []

    for part in xml_parts:
        stripped = part.strip()
        if stripped == "<dict>" or stripped.startswith("<dict>"):
            in_dict = True
        if in_dict:
            dict_preamble += part
            if stripped == "</dict>":
                in_dict = False
            continue
        if stripped.startswith("</pg>"):
            current_seg.append(part)
            page_segments.append("".join(current_seg))
            current_seg = []
        else:
            current_seg.append(part)

    if current_seg:
        page_segments.append("".join(current_seg))

    current_chunk_parts: List[str] = ["<d>"]
    if dict_preamble:
        current_chunk_parts.append(dict_preamble)

    for seg in page_segments:
        candidate = "".join(current_chunk_parts) + seg + f"{nl}</d>"
        cand_toks = count_tokens(candidate)
        if cand_toks > chunk_token_limit and len(current_chunk_parts) > (2 if dict_preamble else 1):
            chunk_str = "".join(current_chunk_parts) + f"{nl}</d>"
            chunks.append({"text": chunk_str, "tokens": count_tokens(chunk_str)})
            current_chunk_parts = ["<d>"]
            if dict_preamble:
                current_chunk_parts.append(dict_preamble)
        current_chunk_parts.append(seg)

    if len(current_chunk_parts) > (2 if dict_preamble else 1):
        chunk_str = "".join(current_chunk_parts) + f"{nl}</d>"
        chunks.append({"text": chunk_str, "tokens": count_tokens(chunk_str)})

    if not chunks:
        chunks = [{"text": xml_content, "tokens": comp_tokens}]

    # ── Token counts ──────────────────────────────────────────
    original_text = "\n".join(original_text_parts)
    orig_tokens = count_tokens(original_text)
    comp_tokens = count_tokens(xml_content)

    # Fallback if structural XML tags bloated the size (common for tiny documents)
    if comp_tokens > orig_tokens:
        xml_content = original_text.strip()
        comp_tokens = orig_tokens
        
        # Chunk the raw text to preserve RAG limit
        chunks = []
        words = xml_content.split(" ")
        current_words = []
        
        for w in words:
            current_words.append(w)
            # Check length heuristically before exact counting to save time
            if len(current_words) > chunk_token_limit * 0.7:
                candidate = " ".join(current_words)
                cand_toks = count_tokens(candidate)
                if cand_toks >= chunk_token_limit:
                    chunks.append({"text": candidate, "tokens": cand_toks})
                    current_words = []
        
        if current_words:
            candidate = " ".join(current_words)
            chunks.append({"text": candidate, "tokens": count_tokens(candidate)})
            
        if not chunks:
            chunks = [{"text": xml_content, "tokens": comp_tokens}]

    emit("done")
    return xml_content, orig_tokens, comp_tokens, chunks
