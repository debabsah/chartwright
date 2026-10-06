#!/usr/bin/env python3
"""Create or update the "Sundown" theme on a Superset 6.x instance, in a palette.

    SUPERSET_URL=https://superset.example.com SUPERSET_PASSWORD=... python3 create_theme.py [palette]

sundown-theme.json holds the type, axes and per-chart overrides; ../palettes/<palette>.json
(default coral-teal-v2) supplies every colour in it. The spec names the theme
("dashboard.theme": "Sundown"); Chartwright doesn't create it.
"""

import json
import os
import sys
from pathlib import Path

import requests

URL = os.environ["SUPERSET_URL"].rstrip("/")
NAME = "Sundown"
CONFIG = json.loads((Path(__file__).parent / "sundown-theme.json").read_text(encoding="utf-8"))
P = json.loads((Path(__file__).parent.parent / "palettes" / f"{sys.argv[1] if len(sys.argv) > 1 else 'coral-teal-v2'}.json")
               .read_text(encoding="utf-8"))
CONFIG["token"].update({"colorPrimary": P["masthead"], "colorLink": P["this"], "colorBgLayout": P["ground"],
                        "colorBgContainer": P["tile"], "colorBorder": P["rule"], "colorBorderSecondary": P["border"],
                        "colorText": P["ink"], "colorTextSecondary": P["sec"]})
E = CONFIG["echartsOptionsOverrides"]
E["legend"]["textStyle"]["color"] = E["xAxis"]["axisLabel"]["color"] = E["yAxis"]["axisLabel"]["color"] = P["sec"]
E["xAxis"]["axisLine"]["lineStyle"]["color"] = P["rule"]
E["yAxis"]["splitLine"]["lineStyle"]["color"] = P["grid"]
E["xAxis"]["nameTextStyle"] = {"color": P["sec"], "fontSize": 12}
E["yAxis"]["nameTextStyle"] = {"color": P["sec"], "fontSize": 12}
CONFIG["echartsOptionsOverridesByChartType"]["box_plot"]["series"]["itemStyle"]["borderColor"] = P["this"]

s = requests.Session()
r = s.post(f"{URL}/api/v1/security/login", json={"username": "admin", "password": os.environ["SUPERSET_PASSWORD"],
                                               "provider": "db"})
r.raise_for_status()
s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
s.headers["X-CSRFToken"] = s.get(f"{URL}/api/v1/security/csrf_token/").json()["result"]
s.headers["Referer"] = URL
themes = s.get(f"{URL}/api/v1/theme/", params={"q": "(page_size:100)"}).json()["result"]
found = [t for t in themes if t["theme_name"] == NAME]
body = {"theme_name": NAME, "json_data": json.dumps(CONFIG)}
r = s.put(f"{URL}/api/v1/theme/{found[0]['id']}", json=body) if found else s.post(f"{URL}/api/v1/theme/", json=body)
if not r.ok:
    sys.exit(f"{r.status_code}: {r.text[:600]}")
print(("updated" if found else "created"), NAME, r.json().get("id", found[0]["id"] if found else None))
