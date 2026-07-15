"""Insights page: cross-dataset analysis — wind vs price, negative prices,
duration curve, and the deepening midday solar dip."""

import dash
import numpy as np
import plotly.graph_objects as go
from dash import html

import queries
from components import chart_card
from theme import AREA_COLORS, AXIS_LINE, BLUE, INK_PRIMARY, SEQUENTIAL_BLUES, base_layout

dash.register_page(__name__, path="/insights", name="Insights", order=3)


def layout():
    return html.Div(
        [
            html.H2("Market insights"),
            html.P(
                "What happens when you join the datasets: wind supply pushes "
                "prices down — sometimes below zero.",
                className="page-intro",
            ),
            chart_card(
                wind_vs_price_figure(),
                queries.wind_vs_price_dk1(),
                note="Each dot is one day in DK1 since 2021. The fit line slopes "
                "down: more forecast wind, lower day-ahead price (merit-order "
                "effect).",
            ),
            chart_card(
                duration_curve_figure(),
                queries.duration_curve(),
                note="Every hour of the last 12 months sorted from most to "
                "least expensive — the classic market view of how extreme the "
                "extremes are. The tail dipping below zero is the hours when "
                "producers paid to keep generating.",
            ),
            chart_card(
                hourly_profile_figure(),
                queries.hourly_profile_by_year(),
                note="Average DK1 price per hour of the day, one line per year "
                "(darker = more recent). The midday dip deepens as solar "
                "capacity grows while the 17–20h evening peak persists — the "
                "emerging Danish duck curve.",
            ),
            chart_card(
                negative_hours_figure(),
                queries.negative_price_hours(),
                note="Hours where the market price itself went below zero — "
                "producers paying consumers to take power. Rare before 2023, "
                "now routine in windy months.",
            ),
        ]
    )


def wind_vs_price_figure():
    df = queries.wind_vs_price_dk1().dropna()
    slope, intercept = np.polyfit(df["wind_mw"], df["avg_price"], 1)
    x_fit = np.array([df["wind_mw"].min(), df["wind_mw"].max()])
    r = np.corrcoef(df["wind_mw"], df["avg_price"])[0, 1]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["wind_mw"], y=df["avg_price"], name="One day",
            mode="markers",
            marker=dict(color=BLUE, size=6, opacity=0.35),
            customdata=df["day"],
            hovertemplate="%{customdata|%Y-%m-%d}: %{x:.0f} MW wind, "
            "%{y:.2f} DKK/kWh<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_fit, y=slope * x_fit + intercept, name="Linear fit",
            mode="lines", line=dict(color=INK_PRIMARY, width=2),
        )
    )
    fig.update_layout(
        base_layout(
            title=f"Forecast wind vs daily average price, DK1 (r = {r:.2f})",
            xaxis_title="Forecast wind (MW, daily avg)",
            yaxis_title="DKK/kWh incl. VAT",
            height=460,
            hovermode="closest",
        )
    )
    return fig


def duration_curve_figure():
    df = queries.duration_curve()
    fig = go.Figure()
    for area, color in AREA_COLORS.items():
        prices = df[df["price_area"] == area]["avg_price"].to_numpy()
        pct = np.arange(1, len(prices) + 1) / len(prices) * 100
        fig.add_trace(
            go.Scatter(
                x=pct, y=prices, name=area,
                mode="lines", line=dict(color=color, width=2),
                hovertemplate="%{x:.0f}% of hours above %{y:.2f} DKK/kWh"
                "<extra>%{fullData.name}</extra>",
            )
        )
    fig.add_hline(y=0, line_color=AXIS_LINE, line_width=1)
    fig.update_layout(
        base_layout(
            title="Price duration curve, last 12 months",
            xaxis_title="% of hours",
            yaxis_title="DKK/kWh incl. VAT",
            height=420,
            hovermode="closest",
        )
    )
    return fig


def hourly_profile_figure():
    df = queries.hourly_profile_by_year()
    years = sorted(df["year"].unique())
    # Ordered series -> sequential ramp (light = oldest), skipping the
    # palest steps so every line clears the surface.
    steps = np.linspace(2, len(SEQUENTIAL_BLUES) - 1, len(years))
    ramp = [SEQUENTIAL_BLUES[int(i)] for i in steps]
    fig = go.Figure()
    for year, color in zip(years, ramp):
        sub = df[df["year"] == year]
        fig.add_trace(
            go.Scatter(
                x=sub["hour_of_day"], y=sub["avg_price"], name=str(year),
                mode="lines", line=dict(color=color, width=2),
            )
        )
    fig.update_layout(
        base_layout(
            title="DK1 average price by hour of day, per year (Danish time)",
            xaxis_title="Hour of day",
            yaxis_title="DKK/kWh incl. VAT",
            height=420,
        )
    )
    fig.update_xaxes(dtick=4)
    return fig


def negative_hours_figure():
    df = queries.negative_price_hours()
    fig = go.Figure()
    for area, color in AREA_COLORS.items():
        sub = df[df["price_area"] == area]
        fig.add_trace(
            go.Bar(
                x=sub["month"], y=sub["negative_hours"], name=area,
                marker=dict(color=color),
            )
        )
    fig.update_layout(
        base_layout(
            title="Negative-price hours per month",
            yaxis_title="Hours",
            height=400,
            barmode="group",
            bargap=0.25,
        )
    )
    return fig
