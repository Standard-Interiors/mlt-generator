import fitz
import io


def extract_text(pdf_path: str) -> str:
    """Extract all text from a PDF file using PyMuPDF."""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pages.append(page.get_text("text"))
    doc.close()
    return "\n\n".join(pages)


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    doc = fitz.open(pdf_path)
    count = doc.page_count
    doc.close()
    return count


def extract_text_pages(pdf_path: str, pages: list[int]) -> dict[int, str]:
    """Extract text from specific pages (1-indexed).

    Returns dict mapping page number to extracted text.
    """
    doc = fitz.open(pdf_path)
    result = {}
    for page_num in pages:
        if 1 <= page_num <= doc.page_count:
            page = doc[page_num - 1]  # fitz uses 0-indexed
            result[page_num] = page.get_text("text")
    doc.close()
    return result


def render_page_image(pdf_path: str, page_num: int, dpi: int = 150) -> bytes:
    """Render a specific page as JPEG bytes (1-indexed page number)."""
    doc = fitz.open(pdf_path)
    if page_num < 1 or page_num > doc.page_count:
        doc.close()
        raise ValueError(f"Page {page_num} out of range (1-{doc.page_count})")

    page = doc[page_num - 1]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("jpeg")
    doc.close()
    return img_bytes


def extract_all_page_text_fast(pdf_path: str) -> dict[int, str]:
    """Extract text from ALL pages quickly. Returns dict of 1-indexed page -> text."""
    doc = fitz.open(pdf_path)
    result = {}
    for i in range(doc.page_count):
        result[i + 1] = doc[i].get_text("text")
    doc.close()
    return result
