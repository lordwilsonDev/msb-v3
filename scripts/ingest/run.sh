#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/lordwilson/msb-v3"
MSB="${MSB_URL:-http://localhost:8766}"

index_document() {
  local tenant_id="$1"
  local text="$2"
  local source="$3"
  local metadata="$4"
  
  curl -s -X POST "${MSB}/rag/index" \
    -H "Content-Type: application/json" \
    -d "{
      \"tenant_id\":\"${tenant_id}\",
      \"documents\":[
        {
          \"text\":\"${text}\",
          \"source\":\"${source}\",
          \"metadata\":${metadata}
        }
      ]
    }" | python3 -m json.tool
}

ingest_pdf() {
  local pdf_path="$1"
  local tenant_id="$2"
  
  echo "[ingest] PDF: ${pdf_path} -> tenant ${tenant_id}"
  
  # Use Marker via isolated Python
  local text
  text=$(PYTHONPATH=/Users/lordwilson/.local/lib/crawl4ai /opt/homebrew/Caskroom/miniforge/base/bin/python3 << 'PY'
import sys
sys.path.insert(0, "/Users/lordwilson/.local/lib/crawl4ai")
import marker
import os

pdf_path = os.environ["PDF_PATH"]
result = marker.convert_single_pdf(pdf_path)
text = result.markdown if hasattr(result, 'markdown') else str(result)
print(text[:4000])
PY
  )
  
  index_document "${tenant_id}" "${text}" "pdf:${pdf_path}" "{}"
}

ingest_web() {
  local url="$1"
  local tenant_id="$2"
  
  echo "[ingest] Web: ${url} -> tenant ${tenant_id}"
  
  # Use Crawl4AI via isolated venv
  local text
  text=$(PYTHONPATH=/Users/lordwilson/.local/lib/crawl4ai /Users/lordwilson/.local/venv/crawl/bin/python3 << 'PY'
import sys, os
sys.path.insert(0, "/Users/lordwilson/.local/lib/crawl4ai")
import asyncio
from crawl4ai import AsyncWebCrawler

async def main():
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=os.environ["CRAWL_URL"])
        text = result.markdown if hasattr(result, 'markdown') else str(result)
        print(text[:4000])

asyncio.run(main())
PY
  )
  
  index_document "${tenant_id}" "${text}" "web:${url}" "{}"
}

chunk_text() {
  local text="$1"
  
  echo "[chunk] Chunking text..."
  
  PYTHONPATH=/Users/lordwilson/.local/lib/crawl4ai /Users/lordwilson/.local/venv/crawl/bin/python3 << 'PY'
import sys
sys.path.insert(0, "/Users/lordwilson/.local/lib/crawl4ai")
import chunky

chunker = chunky.Chunker()
text = sys.argv[1]
chunks = chunker.chunk(text)

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
  --chunk <text>                      Chunk text using Chunky

Examples:
  $0 --pdf ~/Documents/report.pdf acme-corp
  $0 --web https://example.com acme-corp
  $0 --chunk "Long text to chunk..."
EOF
  exit 1
}

# Main
case "${1:-}" in
  --pdf)
    shift
    PDF_PATH="$1" CRAWL_URL="" "$0" --pdf-internal "$@"
    ;;
  --web)
    shift
    CRAWL_URL="$1" "$0" --web-internal "$@"
    ;;
  --chunk)
    shift
    chunk_text "$@"
    ;;
  *)
    show_usage
    ;;
esac
