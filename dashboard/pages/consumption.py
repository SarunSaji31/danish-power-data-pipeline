"""Consumption page: choropleth map + municipality ranking, drill-down
and heating-type split."""

import json
from pathlib import Path

import dash
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

import queries
from components import chart_card, data_table
from municipalities import MUNICIPALITY_NAMES, municipality_name
from population import POPULATION
from theme import AQUA, BLUE, SEQUENTIAL_BLUES, SURFACE, base_layout

dash.register_page(__name__, path="/consumption", name="Consumption", order=2)

# Dataforsyningen (DAGI) polygons, simplified to ~300m tolerance -> 0.3 MB.
# Feature key "kode" is the zero-padded DST code ("0101" = our 101).
# Rings are wound CLOCKWISE: d3-geo (plotly's geo renderer) uses the opposite
# convention of GeoJSON RFC 7946 — a CCW exterior ring fills the whole world.
# Provenance + regeneration steps: data/README.md.
GEOJSON = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "kommuner.geojson")
    .read_text()
)

MAP_METRICS = ["Total GWh", "kWh per resident"]

HEATING_LABELS = {
    "Elvarme eller varmepumpe": "Electric heating / heat pump",
    "Andet": "Other heating",
}
HEATING_COLORS = {
    "Elvarme eller varmepumpe": BLUE,
    "Andet": AQUA,
}


def layout():
    return html.Div(
        [
            html.H2("Private electricity consumption"),
            html.P(
                "Hourly household consumption aggregated from ~30M rows "
                "(98 municipalities × housing type × heating type). These charts "
                "read pre-computed daily aggregates.",
                className="page-intro",
            ),
            chart_card(
                map_figure(MAP_METRICS[0]),
                map_dataframe(),
                title="Household consumption by municipality",
                subtitle="Trailing 12 months — toggle total vs per-resident",
                note="Trailing 12 months. Total volume follows the big cities; "
                "switch to per-resident and the picture inverts — rural "
                "municipalities use more electricity per person (larger homes, "
                "more electric heating).",
                controls=dcc.RadioItems(MAP_METRICS, MAP_METRICS[0],
                                        id="map-metric", inline=True),
                graph_id="map-graph",
            ),
            chart_card(
                top_municipalities_figure(),
                queries.top_municipalities(),
                title="Top 15 municipalities",
                subtitle="Total household consumption, trailing 12 months",
                note="Trailing 12 months, total volume — the big cities lead; "
                "see the map above for the per-resident story.",
            ),
            chart_card(
                municipality_trend_figure(101),
                queries.municipality_monthly(101),
                title=f"Monthly consumption — {municipality_name(101)}",
                subtitle="Household electricity since 2021",
                title_id="muni-title",
                note="Monthly household consumption for the selected "
                "municipality since 2021 — winter peaks show how strongly "
                "heating drives demand.",
                controls=dcc.Dropdown(
                    options=[{"label": name, "value": code}
                             for code, name in sorted(MUNICIPALITY_NAMES.items(),
                                                      key=lambda kv: kv[1])],
                    value=101, clearable=False, id="muni-select",
                ),
                graph_id="muni-graph",
                table_id="muni-table",
            ),
            chart_card(
                heating_figure(),
                queries.monthly_consumption_by_heating(),
                title="Monthly consumption by heating type",
                subtitle="All of Denmark, electric heating vs everything else",
                note="Winter peaks are far steeper for electrically heated homes "
                "— electric heating and heat pumps are what couple Danish "
                "household demand to cold weather. The Aug–Sep 2021 dip is a "
                "hole in the source dataset (energidataservice.dk publishes "
                "almost no data for those weeks), not real consumption.",
            ),
        ]
    )


@callback(Output("map-graph", "figure"), Input("map-metric", "value"))
def update_map(metric):
    return map_figure(metric)


@callback(Output("muni-graph", "figure"), Output("muni-table", "children"),
          Output("muni-title", "children"), Input("muni-select", "value"))
def update_municipality(code):
    return (municipality_trend_figure(code),
            data_table(queries.municipality_monthly(code)),
            f"Monthly consumption — {municipality_name(code)}")


def map_dataframe():
    df = queries.consumption_all_municipalities().copy()
    df["municipality"] = [municipality_name(c) for c in df["municipality_code"]]
    df["kwh_per_resident"] = (df["gwh"] * 1e6
                              / df["municipality_code"].map(POPULATION))
    return df[["municipality_code", "municipality", "gwh", "kwh_per_resident"]]


def map_figure(metric: str):
    df = map_dataframe()
    per_capita = metric == "kWh per resident"
    values = df["kwh_per_resident"] if per_capita else df["gwh"]
    unit = "kWh/resident" if per_capita else "GWh"
    fig = go.Figure(
        go.Choropleth(
            geojson=GEOJSON,
            locations=df["municipality_code"].astype(str).str.zfill(4),
            featureidkey="properties.kode",
            z=values,
            colorscale=SEQUENTIAL_BLUES,
            marker_line_color=SURFACE,
            marker_line_width=0.5,
            colorbar=dict(title=unit, outlinewidth=0),
            customdata=df["municipality"],
            hovertemplate="%{customdata}: %{z:,.0f} " + unit + "<extra></extra>",
        )
    )
    fig.update_geos(fitbounds="locations", visible=False, bgcolor=SURFACE)
    fig.update_layout(
        base_layout(
            height=560,
            hovermode="closest",
            margin=dict(l=8, r=8, t=8, b=8),
        )
    )
    return fig


def municipality_trend_figure(code: int):
    df = queries.municipality_monthly(code)
    fig = go.Figure(
        go.Scatter(
            x=df["month"], y=df["gwh"], mode="lines",
            line=dict(color=BLUE, width=2),
        )
    )
    fig.update_layout(
        base_layout(
            yaxis_title="GWh",
            height=360,
            showlegend=False,
        )
    )
    return fig


def top_municipalities_figure():
    df = queries.top_municipalities().sort_values("gwh")
    labels = [municipality_name(code) for code in df["municipality_code"]]
    fig = go.Figure(
        go.Bar(
            x=df["gwh"], y=labels, orientation="h",
            marker=dict(color=BLUE),
            hovertemplate="%{y}: %{x:.1f} GWh<extra></extra>",
        )
    )
    fig.update_layout(
        base_layout(
            xaxis_title="GWh",
            height=460,
            hovermode="closest",
            bargap=0.35,
        )
    )
    return fig


def heating_figure():
    df = queries.monthly_consumption_by_heating()
    fig = go.Figure()
    for category, color in HEATING_COLORS.items():
        sub = df[df["heating_category"] == category]
        fig.add_trace(
            go.Scatter(
                x=sub["month"], y=sub["gwh"], name=HEATING_LABELS[category],
                mode="lines", line=dict(color=color, width=2),
            )
        )
    fig.update_layout(
        base_layout(
            yaxis_title="GWh",
            height=400,
        )
    )
    return fig
