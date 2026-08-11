#!/usr/bin/env bash
set -euo pipefail

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}" || exit 1
MSB="${MSB_URL:-http://localhost:8766}"
CRAWL_PY="${CRAWL_PY:-$HOME/.local/venv/crawl/bin/python3}"
BASE_PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python3}"
CRAWL4AI_LIB="${CRAWL4AI_LIB:-$HOME/.local/lib/crawl4ai}"
export MSB_RAG_API_KEY="${MSB_RAG_API_KEY:-07bd51761bde7dce3268473773cef30f6ded1062bd7351b33f50863d2d184277}"

index_document() {
  local tenant_id="$1"
  local text="$2"
  local source="$3"
  local metadata="$4"

  "$BASE_PY" - "$tenant_id" "$source" "$text" "$metadata" << 'PY'
import os, sys, json
import urllib.request

msb = "http://localhost:8766"
tenant_id = sys.argv[1]
source = sys.argv[2]
text = sys.argv[3]
metadata = json.loads(sys.argv[4]) if sys.argv[4] else {}

payload = {
    "tenant_id": tenant_id,
    "documents": [
        {
            "text": text,
            "source": source,
            "metadata": metadata,
        }
    ],
}

headers = {"Content-Type": "application/json"}
api_key = os.environ.get("MSB_RAG_API_KEY")
if api_key:
    headers["X-API-Key"] = api_key

req = urllib.request.Request(
    f"{msb}/rag/index",
    data=json.dumps(payload).encode(),
    headers=headers,
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(resp.read().decode())
except Exception as exc:
    print(f"ERROR: {exc}")
    sys.exit(1)
PY
}

chunk_and_index() {
  local tenant_id="$1"
  local source="$2"
  local text="$3"
  local metadata="$4"

  local i=0
  while IFS= read -r -d $'\0' chunk; do
    echo "[chunk] Indexing chunk ${i} of ${source}"
    index_document "${tenant_id}" "${chunk}" "${source}#chunk-${i}" "${metadata}"
    i=$((i + 1))
  done < <("$BASE_PY" - "$text" << 'PY'
import sys

text = sys.argv[1]
chunk_size = 500
overlap = 50
start = 0
while start < len(text):
    end = min(start + chunk_size, len(text))
    sys.stdout.write(text[start:end])
    sys.stdout.write("\0")
    if end >= len(text):
        break
    start = max(end - overlap, start + 1)
PY
  )
}

ingest_pdf() {
  local pdf_path="$1"
  local tenant_id="$2"

  echo "[ingest] PDF: ${pdf_path} -> tenant ${tenant_id}"

  local text
  text=$(CRAWL4AI_LIB="$CRAWL4AI_LIB" PYTHONPATH="$CRAWL4AI_LIB" "$BASE_PY" - "$pdf_path" << 'PY'
import sys, os
sys.path.insert(0, os.environ.get("CRAWL4AI_LIB", ""))
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.output import text_from_rendered

pdf_path = sys.argv[1]
converter = PdfConverter(artifact_dict=create_model_dict())
rendered = converter(pdf_path)
text, _, _ = text_from_rendered(rendered)
print(text)
PY
  )

  chunk_and_index "${tenant_id}" "pdf:${pdf_path}" "${text}" "{}"
}

ingest_web() {
  local url="$1"
  local tenant_id="$2"

  echo "[ingest] Web: ${url} -> tenant ${tenant_id}"

  local text
  text=$(CRAWL4AI_LIB="$CRAWL4AI_LIB" "$CRAWL_PY" - "$url" << 'PY'
import sys, os
sys.path.insert(0, os.environ.get("CRAWL4AI_LIB", ""))
import asyncio
from crawl4ai import AsyncWebCrawler

async def main():
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=sys.argv[1])
        text = result.markdown if hasattr(result, "markdown") else str(result)
        print(text)

asyncio.run(main())
PY
  )

  chunk_and_index "${tenant_id}" "web:${url}" "${text}" "{}"
}

chunk_text() {
  local text="$1"

  echo "[chunk] Chunking text..."

  "$BASE_PY" - "$text" << 'PY'
import sys

text = sys.argv[1]
chunk_size = 500
overlap = 50
chunks = []
start = 0
idx = 0
while start < len(text):
    end = min(start + chunk_size, len(text))
    chunk = text[start:end]
    chunks.append(chunk)
    idx += 1
    if end >= len(text):
        break
    start = max(end - overlap, start + 1)

print(f"Total chunks: {len(chunks)}")
for i, chunk in enumerate(chunks):
    print(f"\n--- Chunk {i+1} ---")
    print(chunk[:200])
PY
}

show_usage() {
  cat << EOF
Usage: $0 <command> [args...]

Commands:
  --pdf <pdf_path> <tenant_id>       Ingest PDF into tenant Qdrant collection
  --web <url> <tenant_id>            Crawl web page and ingest into tenant
  --chunk <text>                     Chunk text using Chunky

Examples:
  $0 --pdf ~/Documents/report.pdf acme-corp
  $0 --web https://example.com acme-corp
  $0 --chunk "Long text to chunk..."
EOF
  exit 1
}

case "${1:-}" in
  --pdf)
    shift
    ingest_pdf "$@"
    ;;
  --web)
    shift
    ingest_web "$@"
    ;;
  --chunk)
    shift
    chunk_text "$@"
    ;;
  *)
    show_usage
    ;;
esac
