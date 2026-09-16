#!/bin/bash
# YT2PDFS startup script
# Usage: ./start.sh YOUR_GEMINI_API_KEY

if [ -z "$1" ] && [ -z "$GEMINI_API_KEY" ]; then
  echo ""
  echo "  ⚠  No Gemini API key provided."
  echo ""
  echo "  Usage:"
  echo "    ./start.sh YOUR_API_KEY"
  echo "  OR"
  echo "    export GEMINI_API_KEY=YOUR_API_KEY && ./start.sh"
  echo ""
  echo "  Get a free key at: https://aistudio.google.com/app/apikey"
  echo ""
  exit 1
fi

if [ -n "$1" ]; then
  export GEMINI_API_KEY="$1"
fi

echo ""
echo "  ✅  Starting YT2PDFS..."
echo "  🌐  Open your browser at: http://localhost:8000"
echo ""

python3 main.py
