"""Wind & CO2 page: renewable forecasts and grid carbon intensity.

Two stacked charts over the same timeline — never a dual-axis overlay —
so the inverse wind/CO2 relationship reads without inventing a shared scale.
"""

import dash
import plotly.graph_objects as go
from dash import html

import queries
from components import chart_card
from theme import AQUA, AREA_COLORS, BLUE, YELLOW, base_layout

dash.register_page(__name__, path="/wind-co2", name="Wind & CO₂", order=1)

SOURCES = [
    ("onshore_mw", "Onshore wind", BLUE),
    ("offshore_mw", "Offshore wind", AQUA),
    ("solar_mw", "Solar", YELLOW),
]


def layout():
    return html.Div(
        [
            html.H2("Wind, solar & CO₂"),
            html.P(
                "Day-ahead production forecasts for Denmark (DK1+DK2) and the "
                "CO₂ intensity of consumed power. Read the two charts together: "
                "windy periods push carbon intensity down.",
                className="page-intro",
            ),
            chart_card(
                production_figure(),
                queries.daily_wind_solar(),
                note="Daily average forecast output. Offshore capacity grows "
                "visibly across the period; solar peaks every summer.",
            ),
            chart_card(
                co2_figure(),
                queries.daily_co2(),
                note="Grams of CO₂ per kWh consumed (5-min data averaged per "
                "day). Compare valleys here with wind peaks above.",
            ),
        ]
    )


def production_figure():
    df = queries.daily_wind_solar()
    fig = go.Figure()
    for column, label, color in SOURCES:
        smoothed = df[column].rolling(7, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=df["day"], y=smoothed, name=label,
                mode="lines", stackgroup="production",
                line=dict(color=color, width=1),
            )
        )
    fig.update_layout(
        base_layout(
            title="Forecast renewable production (7-day average)",
            yaxis_title="MW",
            height=400,
        )
    )
    return fig


def co2_figure():
    df = queries.daily_co2()
    fig = go.Figure()
    for area, color in AREA_COLORS.items():
        sub = df[df["price_area"] == area]
        smoothed = sub["avg_co2"].rolling(7, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=sub["day"], y=smoothed, name=area,
                mode="lines", line=dict(color=color, width=1.5),
            )
        )
    fig.update_layout(
        base_layout(
            title="CO₂ intensity of consumption (7-day average)",
            yaxis_title="g CO₂/kWh",
            height=400,
        )
    )
    return fig
