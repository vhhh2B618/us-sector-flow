#!/usr/bin/env python3
"""Embed pipeline/out/sector_flow.json into repo-root index.html <script id="data">."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # pipeline/
PAGES = ROOT.parent
data = json.loads((ROOT / "out" / "sector_flow.json").read_text(encoding="utf-8"))
html_path = PAGES / "index.html"
html = html_path.read_text(encoding="utf-8")
payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
new_html, n = re.subn(
    r'(<script id="data" type="application/json">)(.*?)(</script>)',
    lambda m: m.group(1) + payload + m.group(3),
    html,
    count=1,
    flags=re.S,
)
if n != 1:
    raise SystemExit("Could not find embed script#data in index.html")
html_path.write_text(new_html, encoding="utf-8")
print(f"updated {html_path} ({html_path.stat().st_size} bytes)")
