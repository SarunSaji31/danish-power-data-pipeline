"""Dash entry point — nav shell + page router.

Pages register themselves from dashboard/pages/ (Dash Pages). Run locally:
    python app.py [port]     -> http://127.0.0.1:8050
In production gunicorn serves `server` (the underlying Flask app).
"""

import sys

import dash
from dash import Dash, dcc, html

app = Dash(__name__, use_pages=True, title="Danish Power Data")
server = app.server

app.layout = html.Div(
    [
        html.Header(
            [
                html.Div("Danish Power Data", className="brand"),
                html.Nav(
                    [
                        dcc.Link(page["name"], href=page["relative_path"])
                        for page in dash.page_registry.values()
                    ]
                ),
            ],
            className="topbar",
        ),
        html.Main(dash.page_container),
        html.Footer(
            "Source: energidataservice.dk · prices incl. 25% VAT · times in UTC "
            "unless noted · charts read TimescaleDB continuous aggregates",
            className="footer",
        ),
    ]
)

if __name__ == "__main__":
    # no debug=True: Dash dev tools crash on Python 3.14 (pkgutil.find_loader removed)
    app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8050)
