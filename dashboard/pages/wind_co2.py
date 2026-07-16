"""Wind & CO2 page: renewable forecasts, production mix and grid carbon.

Two stacked charts over the same timeline — never a dual-axis overlay —
so the inverse wind/CO2 relationship reads without inventing a shared scale.
"""

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import html

import queries
from components import chart_card
from theme import AQUA, AREA_COLORS, BLUE, SURFACE, YELLOW, base_layout

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
                title="Forecast renewable production",
                subtitle="7-day rolling average, stacked by source",
                note="Daily average forecast output. Offshore capacity grows "
                "visibly across the period; solar peaks every summer.",
            ),
            chart_card(
                mix_figure(),
                mix_dataframe(),
                title="Renewable mix, last 12 months",
                subtitle="Share of average forecast output",
                note="Onshore wind still carries most of the Danish renewable "
                "forecast; solar's slice is small on an annual average because "
                "it produces nothing at night and little in winter.",
            ),
            chart_card(
                co2_figure(),
                queries.daily_co2(),
                title="CO₂ intensity of consumption",
                subtitle="7-day rolling average per bidding zone",
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
            yaxis_title="MW",
            height=400,
        )
    )
    fig.update_xaxes(hoverformat="%-d %b %Y")
    fig.update_yaxes(hoverformat=",.0f")
    return fig


def mix_dataframe() -> pd.DataFrame:
    """Average forecast MW per source over the trailing 12 months."""
    df = queries.daily_wind_solar()
    last12 = df[df["day"] >= df["day"].max() - pd.DateOffset(months=12)]
    rows = [(label, last12[column].mean()) for column, label, _ in SOURCES]
    out = pd.DataFrame(rows, columns=["source", "avg_mw"])
    out["share_pct"] = out["avg_mw"] / out["avg_mw"].sum() * 100
    return out


def mix_figure():
    df = mix_dataframe()
    total = df["avg_mw"].sum()
    fig = go.Figure(
        go.Pie(
            labels=df["source"], values=df["avg_mw"],
            hole=0.62, sort=False, direction="clockwise",
            marker=dict(colors=[color for _, _, color in SOURCES],
                        line=dict(color=SURFACE, width=2)),
            textinfo="label+percent", textposition="outside",
            hovertemplate="%{label}: %{value:,.0f} MW avg (%{percent})"
            "<extra></extra>",
        )
    )
    fig.update_layout(
        base_layout(
            height=380,
            showlegend=False,
            margin=dict(l=24, r=24, t=32, b=32),
            annotations=[dict(
                text=f"{total:,.0f} MW<br>avg forecast",
                showarrow=False, font=dict(size=14),
            )],
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
            yaxis_title="g CO₂/kWh",
            height=400,
        )
    )
    fig.update_xaxes(hoverformat="%-d %b %Y")
    fig.update_yaxes(hoverformat=".0f")
    return fig
