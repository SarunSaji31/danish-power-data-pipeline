"""Reusable page building blocks: chart card + accessible data-table twin."""

import pandas as pd
from dash import dcc, html

MAX_TABLE_ROWS = 500


def data_table(df: pd.DataFrame) -> html.Details:
    """Collapsible table twin of a chart, so every value is readable without
    color or hover (capped to keep the DOM small)."""
    shown = df.head(MAX_TABLE_ROWS)
    return html.Details(
        [
            html.Summary(f"View data ({len(df)} rows)"),
            html.Div(
                html.Table(
                    [html.Thead(html.Tr([html.Th(col) for col in shown.columns]))]
                    + [
                        html.Tr([html.Td(_fmt(value)) for value in row])
                        for row in shown.itertuples(index=False)
                    ]
                ),
                className="table-scroll",
            ),
        ],
        className="data-table",
    )


def stat_tile(label: str, value: str, delta: str | None = None,
              delta_class: str = "delta-neutral") -> html.Div:
    """One KPI: label, big value, optional delta vs a named period.
    Delta direction is carried by the arrow/sign in the text, color only
    reinforces it (never color-alone)."""
    children = [
        html.Div(label, className="kpi-label"),
        html.Div(value, className="kpi-value"),
    ]
    if delta:
        children.append(html.Div(delta, className=f"kpi-delta {delta_class}"))
    return html.Div(children, className="kpi-tile")


def kpi_row(tiles: list[html.Div]) -> html.Div:
    """The headline strip above the charts."""
    return html.Div(tiles, className="kpi-row")


def chart_card(figure, df: pd.DataFrame, note: str | None = None,
               controls=None, graph_id: str | None = None,
               table_id: str | None = None, title: str | None = None,
               subtitle: str | None = None,
               title_id: str | None = None) -> html.Div:
    """A chart, an optional caption, and its table twin, on one card.
    controls/graph_id/table_id make the card callback-targetable while the
    table twin stays in sync with what the chart shows.
    title/subtitle render as an HTML card header (figures carry no plotly
    title, so the legend never collides with it); title_id makes the header
    callback-updatable."""
    graph_kwargs = {"figure": figure,
                    "config": {"displayModeBar": False}}
    if graph_id:
        graph_kwargs["id"] = graph_id
    children = []
    if title:
        head = [html.H3(title, id=title_id) if title_id else html.H3(title)]
        if subtitle:
            head.append(html.P(subtitle, className="card-sub"))
        children.append(html.Div(head, className="card-head"))
    if controls is not None:
        children.append(html.Div(controls, className="card-controls"))
    children.append(dcc.Graph(**graph_kwargs))
    if note:
        children.append(html.P(note, className="chart-note"))
    table = data_table(df)
    children.append(html.Div(table, id=table_id) if table_id else table)
    return html.Div(children, className="card")


def _fmt(value):
    if isinstance(value, float):
        return f"{value:,.2f}"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)
