# PDF to Markdown Converter

A high-performance, privacy-focused FastAPI tool designed to convert PDF documents (including complex tables) into clean Markdown format. 

## Key Features
* **In-Memory Processing:** No files are ever saved to disk or database. Processing happens entirely in RAM.
* **Parallel Execution:** Uses `ProcessPoolExecutor` to handle multi-page PDFs efficiently.
* **Table Extraction:** Intelligent detection of structured data and financial tables.
* **Security Built-in:** Includes Rate Limiting, File Size validation, and Page Count limits.

---

## Quick Start (Local)

### 1. Prerequisites
* Python 3.10+
* A virtual environment (`python -m venv venv`)

### 2. Installation
```bash
pip install -r requirements.txt