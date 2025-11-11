#!/bin/bash
cd "$(dirname "$0")"
echo "Installing dependencies…"
brew install python@3.11 poppler || true
pip3 install -r requirements.txt
python3 setup.py py2app
echo ""
echo "✅ Done! Check the 'dist' folder for 'Drive Layout Finder.app'"
open dist
