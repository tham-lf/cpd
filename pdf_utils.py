import httpx
import fitz  # PyMuPDF
import io

async def download_pdf_content(url):
    """Download PDF content from a URL."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(url, timeout=30.0)
            if response.status_code == 200:
                # Basic check if it's actually a PDF
                if b"%PDF-" in response.content[:10]:
                    return response.content
            return None
        except Exception as e:
            print(f"Error downloading PDF from {url}: {e}")
            return None

def extract_text_from_pdf(content):
    """Extract text from PDF binary content."""
    if not content:
        return ""
    try:
        with fitz.open(stream=io.BytesIO(content), filetype="pdf") as doc:
            text = ""
            for page in doc:
                text += page.get_text()
            return text
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
        return ""
