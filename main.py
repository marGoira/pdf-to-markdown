import os
import logging
import re
import asyncio
import time  # Added for the timer
from concurrent.futures import ProcessPoolExecutor
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
import fitz
import uvicorn

# Rate limiting libraries
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Load environment variables
load_dotenv()

# Environment settings
ENV = os.getenv("APP_ENV", "local")
HOST = os.getenv("APP_HOST", "127.0.0.1")
PORT = int(os.getenv("APP_PORT", 8000))
RELOAD = os.getenv("APP_RELOAD", "true").lower() == "true"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 4))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Security limits (Loading from env with defaults)
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", 50)) * 1024 * 1024
MAX_PAGES = int(os.getenv("MAX_PAGES", 300))
RATE_LIMIT = os.getenv("RATE_LIMIT", "2/minute")

# Logging configuration
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("PDF-Converter")

# Rate Limiter setup
limiter = Limiter(key_func=get_remote_address)

description = """
### Privacy & Terms of Use
This API is designed for temporary, volatile processing:
* **No Persistence:** Files are never saved to disk or database.
* **In-Memory:** Everything happens in RAM and is cleared immediately after response.
* **Security Limits:** Max 50MB, 300 pages, and 2 requests/min per user.
"""

app = FastAPI(
    title="PDF to Markdown Converter",
    description=description,
    version="1.1.0"
)

# Trust proxy headers (Essential for Cloud/Proxy IP detection)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Connect rate limiter to FastAPI
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

executor = ProcessPoolExecutor(max_workers=MAX_WORKERS)

# --- Middleware for file size validation ---
@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/convert":
        content_length = request.headers.get('content-length')
        if content_length and int(content_length) > MAX_FILE_SIZE:
            return JSONResponse(
                status_code=413, 
                content={"detail": f"File too large. Maximum allowed size is {MAX_FILE_SIZE // (1024*1024)}MB."}
            )
    return await call_next(request)

def clean_text(text):
    """Clean up hyphenation and extra whitespace from PDF text."""
    if not text: return ""
    text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)
    text = text.replace('\xad', '').replace('\u00ad', '')
    text = re.sub(r'(?<=[a-z])\s(?=[a-z]{2,})', ' ', text)
    return " ".join(text.split())

def process_single_page(page_bytes, page_num):
    """Heavy lifting of PDF extraction per page."""
    try:
        doc = fitz.open(stream=page_bytes, filetype="pdf")
        page = doc[0]
        rect = page.rect
        content_box = fitz.Rect(rect.x0, rect.y0 + 40, rect.x1, rect.y1 - 40)

        # 1. TABLE DETECTION
        tabs = page.find_tables(clip=content_box, strategy="lines", snap_tolerance=3)
        if not tabs.tables:
            tabs = page.find_tables(clip=content_box, strategy="text", vertical_strategy="text", snap_tolerance=4)
        
        table_markdowns = []
        table_bboxes = []
        
        for table in tabs:
            data = table.extract()
            if data:
                valid_cols = [any(row[i] and str(row[i]).strip() for row in data) for i in range(len(data[0]))]
                md = ""
                header_done = False
                for row in data:
                    clean_row = [str(row[i]).replace("\n", " ").strip() if row[i] else "" 
                                 for i, is_valid in enumerate(valid_cols) if is_valid]
                    if not any(clean_row): continue
                    md += "| " + " | ".join(clean_row) + " |\n"
                    if not header_done:
                        md += "| " + " | ".join(["---"] * len(clean_row)) + " |\n"
                        header_done = True
                if md:
                    all_text = md.replace("|", "").replace("-", "")
                    digit_count = sum(c.isdigit() for c in all_text)
                    if digit_count > 5 or len(all_text) < 200:
                        table_markdowns.append(md)
                        table_bboxes.append(table.bbox)

        # 2. TEXT EXTRACTION
        blocks = page.get_text("blocks", clip=content_box)
        blocks.sort(key=lambda b: (b[1], b[0]))
        
        page_text_parts = []
        for b in blocks:
            b_rect = fitz.Rect(b[:4])
            if any(b_rect.intersects(t_bbox) for t_bbox in table_bboxes):
                continue
            text = b[4].strip()
            if text and len(text) > 5:
                page_text_parts.append(clean_text(text))

        res = [f"## Page {page_num + 1}"]
        if table_markdowns:
            res.append("### Tables\n" + "\n".join(table_markdowns))
        if page_text_parts:
            res.append("### Text Content\n" + "\n\n".join(page_text_parts))
            
        doc.close()
        return "\n\n".join(res)
    except Exception as e:
        return f"## Page {page_num + 1}\n\nProcessing error: {str(e)}"

@app.post("/convert")
@limiter.limit(RATE_LIMIT)
async def convert_pdf(request: Request, file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    # --- Start Timer ---
    start_time = time.perf_counter()

    try:
        pdf_bytes = await file.read()
        
        with fitz.open(stream=pdf_bytes, filetype="pdf") as main_doc:
            total_pages = len(main_doc)
            if total_pages > MAX_PAGES:
                logger.warning(f"Rejected: {total_pages} pages (Max: {MAX_PAGES})")
                raise HTTPException(status_code=413, detail=f"PDF too long ({total_pages} pages). Max is {MAX_PAGES}.")
            
            logger.info(f"Processing {total_pages} pages from IP: {request.client.host}")
            tasks = []
            for i in range(total_pages):
                single_doc = fitz.open()
                single_doc.insert_pdf(main_doc, from_page=i, to_page=i)
                tasks.append((single_doc.tobytes(), i))
                single_doc.close()

        # Execute parallel processing
        loop = asyncio.get_event_loop()
        results = await asyncio.gather(*[
            loop.run_in_executor(executor, process_single_page, t[0], t[1]) 
            for t in tasks
        ])

        # --- Stop Timer ---
        end_time = time.perf_counter()
        execution_time = end_time - start_time
        logger.info(f"Conversion successful: {total_pages} pages processed in {execution_time:.2f} seconds.")

        return {
            "pages_processed": len(results),
            "processing_time_sec": round(execution_time, 2),
            "content": "\n---\n".join(results)
        }
    except RateLimitExceeded:
        raise
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Critical Error: {str(e)}")
        raise HTTPException(status_code=500, detail="An error occurred during conversion.")

if __name__ == "__main__":
    logger.info(f"Starting server in {ENV} mode on {HOST}:{PORT}")
    uvicorn.run(
        "main:app", 
        host=HOST, 
        port=PORT, 
        reload=RELOAD if ENV == "local" else False
    )