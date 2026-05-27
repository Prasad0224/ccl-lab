"""
Fundora Auto DD Engine — Document Parsing Utilities
====================================================
Robust parsing functions for all document types accepted by the Auto DD engine.

Supported formats:
    - PDF  → Text extraction (pdfplumber primary, PyMuPDF fallback) + table extraction
    - CSV  → Pandas DataFrame
    - XLSX → Pandas DataFrame (multi-sheet aware)

Every function is wrapped in try/except to ensure the pipeline never crashes
on a single malformed file. Errors are captured and returned alongside
partial results.

Usage:
    from utils.parsers import parse_uploaded_file, classify_document

    result = await parse_uploaded_file(upload_file)
    doc_type = classify_document(result.filename, result.raw_text)
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import UploadFile

from models.schemas import ParsedDocument
from services.stages import DocumentType

logger = logging.getLogger("auto_dd.parsers")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Supported file extensions
SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_TABULAR_EXTENSIONS = {".csv", ".xlsx", ".xls"}
ALL_SUPPORTED_EXTENSIONS = SUPPORTED_PDF_EXTENSIONS | SUPPORTED_TABULAR_EXTENSIONS

# Maximum text length to extract from a single PDF (prevent memory issues)
MAX_PDF_TEXT_LENGTH = 500_000  # ~500K chars

# Document type classification keywords (case-insensitive)
# Each key is a DocumentType, each value is a list of keyword patterns
_CLASSIFICATION_KEYWORDS: Dict[str, List[str]] = {
    DocumentType.PITCH_DECK: [
        r"pitch\s*deck", r"investor\s*deck", r"startup\s*deck",
        r"tam\b", r"sam\b", r"som\b", r"problem\s*statement",
        r"solution\b", r"go.to.market", r"traction", r"founding\s*team",
        r"business\s*model", r"competitive\s*landscape", r"ask\b.*fund",
    ],
    DocumentType.CAP_TABLE: [
        r"cap\s*table", r"capitalization", r"shareholder",
        r"esop", r"equity\s*split", r"vesting", r"dilution",
        r"share\s*class", r"preferred\s*stock", r"common\s*stock",
    ],
    DocumentType.KPI_SHEET: [
        r"kpi", r"key\s*performance", r"metrics\s*sheet",
        r"dau\b", r"mau\b", r"wau\b", r"retention",
        r"churn", r"arpu\b", r"active\s*users",
    ],
    DocumentType.FINANCIAL_PROJECTIONS: [
        r"projection", r"forecast", r"pro\s*forma",
        r"projected\s*revenue", r"financial\s*model",
        r"3.year", r"5.year", r"growth\s*scenario",
    ],
    DocumentType.HISTORICAL_PNL: [
        r"p\s*&?\s*l\b", r"profit\s*(and|&)\s*loss", r"income\s*statement",
        r"revenue\b.*expense", r"gross\s*profit", r"net\s*income",
        r"operating\s*expense", r"ebitda",
    ],
    DocumentType.BALANCE_SHEET: [
        r"balance\s*sheet", r"assets\b.*liabilities",
        r"current\s*assets", r"total\s*equity",
        r"accounts\s*receivable", r"accounts\s*payable",
    ],
    DocumentType.CASH_FLOW: [
        r"cash\s*flow", r"operating\s*activities",
        r"investing\s*activities", r"financing\s*activities",
        r"net\s*cash", r"free\s*cash\s*flow",
    ],
    DocumentType.BANK_STATEMENTS: [
        r"bank\s*statement", r"account\s*statement",
        r"transaction\s*history", r"credit\b.*debit",
        r"opening\s*balance", r"closing\s*balance",
        r"narration", r"ifsc",
    ],
    DocumentType.GST_RETURNS: [
        r"gst\s*return", r"gstin", r"gstr",
        r"taxable\s*value", r"igst", r"cgst", r"sgst",
        r"input\s*tax\s*credit", r"output\s*tax",
    ],
}

# Filename-based classification patterns (checked BEFORE content analysis)
_FILENAME_PATTERNS: Dict[str, List[str]] = {
    DocumentType.PITCH_DECK: [r"pitch", r"deck", r"investor"],
    DocumentType.CAP_TABLE: [r"cap.?table", r"captable", r"equity"],
    DocumentType.KPI_SHEET: [r"kpi", r"metrics"],
    DocumentType.FINANCIAL_PROJECTIONS: [r"projection", r"forecast", r"pro.?forma"],
    DocumentType.HISTORICAL_PNL: [r"p.?n.?l", r"profit", r"income.?statement"],
    DocumentType.BALANCE_SHEET: [r"balance"],
    DocumentType.CASH_FLOW: [r"cash.?flow"],
    DocumentType.BANK_STATEMENTS: [r"bank", r"statement"],
    DocumentType.GST_RETURNS: [r"gst", r"gstr"],
}


# ---------------------------------------------------------------------------
# File Saving
# ---------------------------------------------------------------------------

async def save_upload_temp(
    upload_file: UploadFile,
    temp_dir: str = "tmp_uploads",
) -> Path:
    """
    Save an uploaded file to a temporary directory.
    
    Creates the temp directory if it doesn't exist. Uses a UUID prefix
    to avoid filename collisions.
    
    Args:
        upload_file: FastAPI UploadFile object.
        temp_dir: Directory to save temporary files in.
        
    Returns:
        Path to the saved file.
        
    Raises:
        IOError: If the file cannot be written to disk.
    """
    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    # Sanitize filename and add UUID prefix to prevent collisions
    safe_name = re.sub(r"[^\w.\-]", "_", upload_file.filename or "unknown")
    unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    file_path = temp_path / unique_name

    try:
        content = await upload_file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        # Reset file position for potential re-reads
        await upload_file.seek(0)
        logger.info("Saved upload to %s (%d bytes)", file_path, len(content))
        return file_path
    except Exception as e:
        logger.error("Failed to save upload '%s': %s", upload_file.filename, e)
        raise IOError(f"Failed to save uploaded file '{upload_file.filename}': {e}") from e


def cleanup_temp_files(file_paths: List[Path]) -> None:
    """
    Remove temporary files after processing.
    
    Silently ignores files that have already been deleted.
    """
    for path in file_paths:
        try:
            if path.exists():
                path.unlink()
                logger.debug("Cleaned up temp file: %s", path)
        except Exception as e:
            logger.warning("Failed to clean up %s: %s", path, e)


# ---------------------------------------------------------------------------
# PDF Parsing
# ---------------------------------------------------------------------------

def parse_pdf_text(file_path: Path) -> Tuple[str, List[str]]:
    """
    Extract text from a PDF file using pdfplumber (primary parser).
    
    If pdfplumber fails or returns empty text, falls back to PyMuPDF (fitz).
    
    Args:
        file_path: Path to the PDF file.
        
    Returns:
        Tuple of (extracted_text, list_of_errors).
        Even on partial failure, returns whatever text was successfully extracted.
    """
    errors: List[str] = []
    text = ""

    # --- Primary: pdfplumber ---
    try:
        import pdfplumber

        with pdfplumber.open(str(file_path)) as pdf:
            pages_text = []
            for i, page in enumerate(pdf.pages):
                try:
                    page_text = page.extract_text() or ""
                    pages_text.append(page_text)
                except Exception as e:
                    err_msg = f"pdfplumber: Failed to extract page {i + 1}: {e}"
                    logger.warning(err_msg)
                    errors.append(err_msg)

            text = "\n\n".join(pages_text)

        if text.strip():
            logger.info(
                "pdfplumber extracted %d chars from %s (%d pages)",
                len(text), file_path.name, len(pdf.pages),
            )
            # Truncate if excessively long
            if len(text) > MAX_PDF_TEXT_LENGTH:
                text = text[:MAX_PDF_TEXT_LENGTH]
                errors.append(
                    f"Text truncated to {MAX_PDF_TEXT_LENGTH} chars (original was longer)."
                )
            return text, errors

    except ImportError:
        errors.append("pdfplumber not installed, trying PyMuPDF fallback.")
    except Exception as e:
        err_msg = f"pdfplumber failed on '{file_path.name}': {e}"
        logger.warning(err_msg)
        errors.append(err_msg)

    # --- Fallback: PyMuPDF (fitz) ---
    try:
        text = _parse_pdf_with_pymupdf(file_path)
        if text.strip():
            logger.info(
                "PyMuPDF fallback extracted %d chars from %s",
                len(text), file_path.name,
            )
            if len(text) > MAX_PDF_TEXT_LENGTH:
                text = text[:MAX_PDF_TEXT_LENGTH]
                errors.append(
                    f"Text truncated to {MAX_PDF_TEXT_LENGTH} chars (original was longer)."
                )
            return text, errors
    except ImportError:
        errors.append("PyMuPDF (fitz) not installed. No PDF text extraction available.")
    except Exception as e:
        err_msg = f"PyMuPDF fallback also failed on '{file_path.name}': {e}"
        logger.error(err_msg)
        errors.append(err_msg)

    if not text.strip():
        errors.append(
            f"Could not extract any text from '{file_path.name}'. "
            "The PDF may be image-only (scanned) or corrupted."
        )

    return text, errors


def _parse_pdf_with_pymupdf(file_path: Path) -> str:
    """
    Fallback PDF text extraction using PyMuPDF (fitz).
    
    PyMuPDF is generally faster than pdfplumber and handles some PDFs better,
    but produces less structured output for tables.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(file_path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def parse_pdf_tables(file_path: Path) -> Tuple[List[pd.DataFrame], List[str]]:
    """
    Extract tables from a PDF file using pdfplumber.
    
    Returns a list of DataFrames (one per detected table) and any errors.
    Tables with fewer than 2 rows are skipped (likely false positives).
    
    Args:
        file_path: Path to the PDF file.
        
    Returns:
        Tuple of (list_of_dataframes, list_of_errors).
    """
    errors: List[str] = []
    tables: List[pd.DataFrame] = []

    try:
        import pdfplumber

        with pdfplumber.open(str(file_path)) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    page_tables = page.extract_tables()
                    if not page_tables:
                        continue
                    for j, table_data in enumerate(page_tables):
                        if not table_data or len(table_data) < 2:
                            continue  # Skip empty or single-row tables

                        # Use the first row as column headers
                        headers = table_data[0]
                        rows = table_data[1:]

                        # Clean up headers: replace None with column index
                        clean_headers = []
                        for k, h in enumerate(headers):
                            if h is None or str(h).strip() == "":
                                clean_headers.append(f"col_{k}")
                            else:
                                clean_headers.append(str(h).strip())

                        df = pd.DataFrame(rows, columns=clean_headers)

                        # Drop completely empty rows
                        df = df.dropna(how="all")

                        if not df.empty:
                            tables.append(df)
                            logger.debug(
                                "Extracted table from page %d, table %d: %d rows × %d cols",
                                i + 1, j + 1, len(df), len(df.columns),
                            )

                except Exception as e:
                    err_msg = f"Table extraction failed on page {i + 1}: {e}"
                    logger.warning(err_msg)
                    errors.append(err_msg)

        logger.info(
            "Extracted %d tables from %s", len(tables), file_path.name
        )

    except ImportError:
        errors.append("pdfplumber not installed. Table extraction unavailable.")
    except Exception as e:
        err_msg = f"Table extraction failed for '{file_path.name}': {e}"
        logger.error(err_msg)
        errors.append(err_msg)

    return tables, errors


# ---------------------------------------------------------------------------
# CSV / Excel Parsing
# ---------------------------------------------------------------------------

def parse_csv(file_path: Path) -> Tuple[pd.DataFrame, List[str]]:
    """
    Parse a CSV file into a Pandas DataFrame.
    
    Handles common CSV issues:
        - Multiple encodings (UTF-8, Latin-1, CP1252)
        - Leading/trailing whitespace in headers and values
        - Empty rows and columns
    
    Args:
        file_path: Path to the CSV file.
        
    Returns:
        Tuple of (DataFrame, list_of_errors).
        Returns an empty DataFrame on total failure.
    """
    errors: List[str] = []

    # Try multiple encodings
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    df = None

    for encoding in encodings:
        try:
            df = pd.read_csv(
                str(file_path),
                encoding=encoding,
                skipinitialspace=True,
                on_bad_lines="warn",
            )
            logger.info(
                "Parsed CSV '%s' with %s encoding: %d rows × %d cols",
                file_path.name, encoding, len(df), len(df.columns),
            )
            break
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            errors.append(f"CSV file '{file_path.name}' is empty.")
            return pd.DataFrame(), errors
        except Exception as e:
            errors.append(f"CSV parse error with {encoding}: {e}")
            continue

    if df is None:
        errors.append(
            f"Could not parse CSV '{file_path.name}' with any supported encoding."
        )
        return pd.DataFrame(), errors

    # Clean up the DataFrame
    df = _clean_dataframe(df, file_path.name, errors)
    return df, errors


def parse_excel(
    file_path: Path,
    sheet_name: Optional[str] = None,
) -> Tuple[Dict[str, pd.DataFrame], List[str]]:
    """
    Parse an Excel file (.xlsx/.xls) into a dict of DataFrames (one per sheet).
    
    If ``sheet_name`` is provided, only that sheet is parsed.
    Otherwise, all sheets are parsed and returned keyed by sheet name.
    
    Args:
        file_path: Path to the Excel file.
        sheet_name: Optional specific sheet to parse. None = all sheets.
        
    Returns:
        Tuple of (dict_of_dataframes, list_of_errors).
        Keys are sheet names, values are DataFrames.
    """
    errors: List[str] = []
    result: Dict[str, pd.DataFrame] = {}

    try:
        # Read all sheets (or specific sheet) into a dict
        target = sheet_name if sheet_name else None
        raw = pd.read_excel(
            str(file_path),
            sheet_name=target,
            engine="openpyxl",
        )

        # pd.read_excel returns a DataFrame if sheet_name is a string,
        # or a dict of DataFrames if sheet_name is None
        if isinstance(raw, pd.DataFrame):
            key = sheet_name or "Sheet1"
            raw = {key: raw}

        for name, df in raw.items():
            df = _clean_dataframe(df, f"{file_path.name}[{name}]", errors)
            if not df.empty:
                result[name] = df
                logger.info(
                    "Parsed Excel sheet '%s' from '%s': %d rows × %d cols",
                    name, file_path.name, len(df), len(df.columns),
                )

        if not result:
            errors.append(f"Excel file '{file_path.name}' contains no data.")

    except FileNotFoundError:
        errors.append(f"Excel file not found: '{file_path.name}'.")
    except Exception as e:
        err_msg = f"Excel parse error for '{file_path.name}': {e}"
        logger.error(err_msg)
        errors.append(err_msg)

    return result, errors


def _clean_dataframe(
    df: pd.DataFrame,
    source_name: str,
    errors: List[str],
) -> pd.DataFrame:
    """
    Clean a DataFrame:
        - Strip whitespace from column names
        - Drop fully empty rows and columns
        - Convert numeric-looking strings to numbers
    """
    try:
        # Strip column name whitespace
        df.columns = [
            str(col).strip() if col is not None else f"col_{i}"
            for i, col in enumerate(df.columns)
        ]

        # Drop fully empty rows and columns
        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")

        # Strip whitespace from string values
        for col in df.select_dtypes(include=["object"]).columns:
            df[col] = df[col].apply(
                lambda x: str(x).strip() if pd.notna(x) else x
            )

        # Try to convert numeric-looking columns
        for col in df.columns:
            if df[col].dtype == "object":
                # Remove currency symbols and commas for numeric detection
                cleaned = df[col].apply(_try_clean_numeric)
                numeric = pd.to_numeric(cleaned, errors="coerce")
                # If more than 50% of non-null values converted successfully, use numeric
                if numeric.notna().sum() > 0.5 * df[col].notna().sum():
                    df[col] = numeric

    except Exception as e:
        errors.append(f"Warning: DataFrame cleanup failed for {source_name}: {e}")

    return df


def _try_clean_numeric(val: Any) -> Any:
    """
    Attempt to clean a value for numeric conversion.
    Removes currency symbols (₹, $, €), commas, and percentage signs.
    """
    if pd.isna(val):
        return val
    s = str(val).strip()
    # Remove common currency symbols and formatting
    s = re.sub(r"[₹$€£¥,]", "", s)
    # Handle percentage values
    s = re.sub(r"\s*%\s*$", "", s)
    # Handle parenthetical negatives: (100) → -100
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return s if s else val


# ---------------------------------------------------------------------------
# Document Type Classification
# ---------------------------------------------------------------------------

def classify_document(
    filename: str,
    content: Optional[str] = None,
    tables: Optional[List[pd.DataFrame]] = None,
) -> str:
    """
    Classify a document into a known document type using heuristics.
    
    Classification strategy (in priority order):
        1. Filename pattern matching (fast, high confidence)
        2. Content keyword analysis (slower, used when filename is ambiguous)
        3. Table structure analysis (for tabular files)
        4. Falls back to DocumentType.OTHER
    
    Args:
        filename: Original filename of the document.
        content: Extracted text content (for PDFs).
        tables: Extracted tables (for tabular files).
        
    Returns:
        One of the DocumentType string constants.
    """
    filename_lower = filename.lower()

    # --- Step 1: Filename-based classification ---
    best_match = _classify_by_filename(filename_lower)
    if best_match is not None:
        logger.info("Classified '%s' as '%s' (filename match)", filename, best_match)
        return best_match

    # --- Step 2: Content-based classification ---
    if content and content.strip():
        best_match = _classify_by_content(content)
        if best_match is not None:
            logger.info("Classified '%s' as '%s' (content match)", filename, best_match)
            return best_match

    # --- Step 3: Table-based classification ---
    if tables:
        best_match = _classify_by_tables(tables)
        if best_match is not None:
            logger.info("Classified '%s' as '%s' (table structure match)", filename, best_match)
            return best_match

    logger.warning("Could not classify '%s', defaulting to OTHER", filename)
    return DocumentType.OTHER


def _classify_by_filename(filename_lower: str) -> Optional[str]:
    """Classify by matching filename against known patterns."""
    scores: Dict[str, int] = {}

    for doc_type, patterns in _FILENAME_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, filename_lower):
                scores[doc_type] = scores.get(doc_type, 0) + 1

    if scores:
        return max(scores, key=scores.get)
    return None


def _classify_by_content(content: str) -> Optional[str]:
    """Classify by counting keyword matches in text content."""
    content_lower = content.lower()
    # Only analyze first 10K chars for performance
    content_sample = content_lower[:10_000]

    scores: Dict[str, int] = {}

    for doc_type, patterns in _CLASSIFICATION_KEYWORDS.items():
        for pattern in patterns:
            matches = len(re.findall(pattern, content_sample))
            if matches:
                scores[doc_type] = scores.get(doc_type, 0) + matches

    if scores:
        # Return the type with the highest match count,
        # but only if it has at least 2 keyword matches (avoid false positives)
        best = max(scores, key=scores.get)
        if scores[best] >= 2:
            return best

    return None


def _classify_by_tables(tables: List[pd.DataFrame]) -> Optional[str]:
    """
    Classify based on table column names.
    
    Useful for CSV/Excel files where content analysis isn't applicable.
    """
    all_columns: List[str] = []
    for df in tables:
        all_columns.extend([str(c).lower() for c in df.columns])

    col_text = " ".join(all_columns)

    # Check for cap table indicators
    cap_table_keywords = {"shareholder", "shares", "equity", "esop", "vesting", "dilution"}
    if len(cap_table_keywords & set(col_text.split())) >= 2:
        return DocumentType.CAP_TABLE

    # Check for KPI indicators
    kpi_keywords = {"month", "dau", "mau", "retention", "churn", "arpu", "users"}
    if len(kpi_keywords & set(col_text.split())) >= 2:
        return DocumentType.KPI_SHEET

    # Check for P&L indicators
    pnl_keywords = {"revenue", "expense", "profit", "loss", "margin", "ebitda"}
    if len(pnl_keywords & set(col_text.split())) >= 2:
        return DocumentType.HISTORICAL_PNL

    # Check for balance sheet indicators
    bs_keywords = {"assets", "liabilities", "equity", "receivable", "payable"}
    if len(bs_keywords & set(col_text.split())) >= 2:
        return DocumentType.BALANCE_SHEET

    # Check for bank statement indicators
    bank_keywords = {"date", "debit", "credit", "balance", "narration", "transaction"}
    if len(bank_keywords & set(col_text.split())) >= 3:
        return DocumentType.BANK_STATEMENTS

    # Check for GST indicators
    gst_keywords = {"gstin", "taxable", "igst", "cgst", "sgst", "gst"}
    if len(gst_keywords & set(col_text.split())) >= 2:
        return DocumentType.GST_RETURNS

    return None


# ---------------------------------------------------------------------------
# Heuristic Pitch Deck Quality Gate
# ---------------------------------------------------------------------------

# Minimum number of VC-standard keywords that must appear in the first 5,000
# characters of a PDF for it to be accepted as a valid pitch deck.
_PITCH_DECK_VC_KEYWORDS = [
    "market", "revenue", "team", "problem", "solution",
    "competition", "traction", "product", "funding", "growth",
]
_PITCH_DECK_MIN_KEYWORD_MATCHES = 2
_PITCH_DECK_SCAN_CHARS = 5_000

_PITCH_DECK_FILENAME_SIGNALS = [
    r"pitch", r"deck", r"investor\s*pres", r"startup\s*pres",
]


def _filename_suggests_pitch_deck(filename: str) -> bool:
    """
    Return True if the filename strongly implies the file is a pitch deck.
    Only applies the quality gate to explicitly labelled pitch decks.
    """
    fn_lower = filename.lower()
    return any(re.search(pat, fn_lower) for pat in _PITCH_DECK_FILENAME_SIGNALS)


def _validate_pitch_deck_content(raw_text: str, filename: str) -> Optional[str]:
    """
    Scan the first 5,000 characters of extracted PDF text for VC-standard keywords.

    Returns:
        None if the document passes the quality gate.
        A rejection error string if the document lacks sufficient keyword density.
    """
    sample = raw_text[:_PITCH_DECK_SCAN_CHARS].lower()
    matched = [kw for kw in _PITCH_DECK_VC_KEYWORDS if kw in sample]

    if len(matched) < _PITCH_DECK_MIN_KEYWORD_MATCHES:
        missing = list(set(_PITCH_DECK_VC_KEYWORDS) - set(matched))[:5]
        return (
            f"Quality Gate Rejected '{filename}': This PDF does not appear to be a valid "
            f"Pitch Deck. Only {len(matched)} of the required {_PITCH_DECK_MIN_KEYWORD_MATCHES} "
            f"VC-standard keywords were found in the document. "
            f"Expected keywords such as: {', '.join(missing)}. "
            f"Please upload the correct Pitch Deck document."
        )
    return None


# ---------------------------------------------------------------------------
# High-Level Parse Function
# ---------------------------------------------------------------------------

async def parse_uploaded_file(
    upload_file: UploadFile,
    temp_dir: str = "tmp_uploads",
) -> ParsedDocument:
    """
    Parse a single uploaded file into a structured ParsedDocument.
    
    This is the main entry point for the parsing pipeline. It:
        1. Saves the file to a temp directory
        2. Detects the file type from the extension
        3. Runs the appropriate parser (PDF or tabular)
        4. Classifies the document type using heuristics
        5. Returns a ParsedDocument with all extracted data
    
    The function NEVER raises — all errors are captured in
    ``ParsedDocument.parse_errors`` so the pipeline can continue
    processing other files even if one fails.
    
    Args:
        upload_file: FastAPI UploadFile object.
        temp_dir: Directory for temporary file storage.
        
    Returns:
        ParsedDocument with extracted text, tables, and classification.
    """
    filename = upload_file.filename or "unknown_file"
    errors: List[str] = []
    raw_text: Optional[str] = None
    tables_data: Optional[List[dict]] = None
    metadata: Dict[str, Any] = {}
    file_path: Optional[Path] = None

    try:
        # --- Save to disk ---
        file_path = await save_upload_temp(upload_file, temp_dir)
        ext = file_path.suffix.lower()
        metadata["extension"] = ext
        metadata["size_bytes"] = file_path.stat().st_size

        if ext not in ALL_SUPPORTED_EXTENSIONS:
            errors.append(
                f"Unsupported file extension '{ext}'. "
                f"Supported: {', '.join(sorted(ALL_SUPPORTED_EXTENSIONS))}"
            )
            return ParsedDocument(
                filename=filename,
                document_type=DocumentType.OTHER,
                parse_errors=errors,
                metadata=metadata,
            )

        # --- Parse based on extension ---
        tables_list: List[pd.DataFrame] = []

        if ext in SUPPORTED_PDF_EXTENSIONS:
            # Extract text
            raw_text, text_errors = parse_pdf_text(file_path)
            errors.extend(text_errors)

            # --- Heuristic Quality Gate: Pitch Deck Content Scan ---
            # If the filename suggests this is a pitch deck, verify it contains
            # VC-standard keywords to catch bogus / wrong uploads early.
            if raw_text and _filename_suggests_pitch_deck(filename):
                rejection = _validate_pitch_deck_content(raw_text, filename)
                if rejection:
                    errors.append(rejection)
                    # Return early — do not parse tables or pass to LLM
                    return ParsedDocument(
                        filename=filename,
                        document_type=DocumentType.OTHER,
                        parse_errors=errors,
                        metadata=metadata,
                    )

            # Extract tables
            pdf_tables, table_errors = parse_pdf_tables(file_path)
            errors.extend(table_errors)
            tables_list = pdf_tables

        elif ext == ".csv":
            df, csv_errors = parse_csv(file_path)
            errors.extend(csv_errors)
            if not df.empty:
                tables_list = [df]
                # Generate text summary of the CSV for content classification
                raw_text = _dataframe_to_text_summary(df, filename)

        elif ext in {".xlsx", ".xls"}:
            sheets, xlsx_errors = parse_excel(file_path)
            errors.extend(xlsx_errors)
            for sheet_name, df in sheets.items():
                tables_list.append(df)
                metadata[f"sheet_{sheet_name}_shape"] = list(df.shape)
            # Generate text summary for classification
            if tables_list:
                raw_text = _dataframe_to_text_summary(tables_list[0], filename)

        # Convert DataFrames to serializable dicts
        if tables_list:
            tables_data = []
            for df in tables_list:
                try:
                    # Convert any NaN values to None for proper JSON serialization
                    clean_df = df.astype(object).where(pd.notna(df), None)
                    tables_data.append(clean_df.to_dict(orient="records"))
                except Exception as e:
                    errors.append(f"DataFrame serialization error: {e}")

        # --- Classify document type ---
        doc_type = classify_document(
            filename=filename,
            content=raw_text,
            tables=tables_list if tables_list else None,
        )
        metadata["classified_by"] = "heuristic"

    except Exception as e:
        err_msg = f"Unexpected error parsing '{filename}': {e}"
        logger.error(err_msg, exc_info=True)
        errors.append(err_msg)
        doc_type = DocumentType.OTHER
    finally:
        # Clean up temp file
        if file_path and file_path.exists():
            try:
                file_path.unlink()
                logger.debug("Cleaned up temp file: %s", file_path)
            except Exception:
                pass

    return ParsedDocument(
        filename=filename,
        document_type=doc_type,
        raw_text=raw_text,
        tables=tables_data,
        parse_errors=errors,
        metadata=metadata,
    )


def _dataframe_to_text_summary(df: pd.DataFrame, source: str) -> str:
    """
    Generate a text summary of a DataFrame for content classification
    and LLM consumption.
    
    Includes column names and the first 20 rows of data.
    """
    lines = [f"=== Data from: {source} ==="]
    lines.append(f"Columns: {', '.join(str(c) for c in df.columns)}")
    lines.append(f"Rows: {len(df)}")
    lines.append("")

    # Include first 20 rows as a text table
    try:
        sample = df.head(20).to_string(index=False, max_colwidth=60)
        lines.append(sample)
    except Exception:
        lines.append("[Could not render data preview]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Utility: Extract numeric values from parsed data
# ---------------------------------------------------------------------------

def extract_numeric_value(
    df: pd.DataFrame,
    column: str,
    row_label: Optional[str] = None,
    row_index: int = -1,
) -> Optional[float]:
    """
    Safely extract a single numeric value from a DataFrame.
    
    Useful for pulling specific metrics (e.g., "Total Revenue" from a P&L).
    
    Args:
        df: Source DataFrame.
        column: Column name to read from.
        row_label: If provided, search for a row where any column contains this text.
        row_index: If row_label is not found, use this row index (-1 = last row).
        
    Returns:
        The numeric value, or None if extraction fails.
    """
    try:
        if column not in df.columns:
            return None

        if row_label:
            # Search for row containing the label in any column
            for col in df.columns:
                mask = df[col].astype(str).str.contains(
                    row_label, case=False, na=False
                )
                if mask.any():
                    idx = mask.idxmax()
                    val = df.loc[idx, column]
                    return _safe_float(val)

        # Fall back to row index
        if abs(row_index) <= len(df):
            val = df.iloc[row_index][column]
            return _safe_float(val)

    except Exception as e:
        logger.debug("extract_numeric_value failed: %s", e)

    return None


def find_column_by_keywords(
    df: pd.DataFrame,
    keywords: List[str],
) -> Optional[str]:
    """
    Find a DataFrame column whose name matches any of the given keywords.
    
    Case-insensitive partial matching.
    
    Args:
        df: Source DataFrame.
        keywords: List of keyword patterns to search for.
        
    Returns:
        Matching column name, or None.
    """
    for col in df.columns:
        col_lower = str(col).lower()
        for keyword in keywords:
            if keyword.lower() in col_lower:
                return col
    return None


def _safe_float(val: Any) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        # Clean the value first
        cleaned = _try_clean_numeric(val)
        return float(cleaned)
    except (ValueError, TypeError):
        return None
