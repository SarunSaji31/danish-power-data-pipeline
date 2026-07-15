"""Prices page: KPI row + latest day-ahead curve + daily trend + heatmap."""

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

import queries
from components import chart_card, kpi_row, stat_tile
from theme import AREA_COLORS, SEQUENTIAL_BLUES, base_layout

dash.register_page(__name__, path="/", name="Prices", order=0)

TREND_RANGES = ["All", "Last 12 months", "2022 crisis"]


def layout():
    return html.Div(
        [
            html.H2("Electricity prices"),
            html.P(
                "Consumer day-ahead price (DKK/kWh incl. 25% VAT) for the two "
                "Danish bidding zones — DK1 west of the Great Belt, DK2 east.",
                className="page-intro",
            ),
            kpi_tiles(),
            chart_card(
                day_ahead_figure(),
                queries.latest_day_ahead(),
                note="The newest day-ahead auction result in the data — the "
                "hourly prices consumers face on that day. Published ~13:00 "
                "CET the day before delivery.",
            ),
            chart_card(
                daily_trend_figure("All"),
                queries.daily_prices(),
                note="The 2022 spike is the European energy crisis; prices have "
                "since fallen back but stay more volatile than pre-crisis.",
                controls=dcc.RadioItems(TREND_RANGES, "All", id="trend-range",
                                        inline=True),
                graph_id="trend-graph",
            ),
            chart_card(
                heatmap_figure(),
                queries.price_heatmap_dk1(),
                note="Each column is a month, each row an hour of the day "
                "(Danish time). The dark evening band at 17–20h is the daily "
                "demand peak; cheap midday hours appear as solar grows.",
            ),
        ]
    )


@callback(Output("trend-graph", "figure"), Input("trend-range", "value"))
def update_trend(range_name):
    return daily_trend_figure(range_name)


def day_ahead_figure():
    df = queries.latest_day_ahead()
    local = df["hour"].dt.tz_convert("Europe/Copenhagen")
    day_label = local.iloc[0].strftime("%A %-d %B")
    fig = go.Figure()
    for area, color in AREA_COLORS.items():
        sub = df[df["price_area"] == area]
        fig.add_trace(
            go.Scatter(
                x=sub["hour"].dt.tz_convert("Europe/Copenhagen"),
                y=sub["avg_price"], name=area,
                mode="lines", line=dict(color=color, width=2, shape="hv"),
            )
        )
    fig.update_layout(
        base_layout(
            title=f"Latest day-ahead prices — {day_label}",
            yaxis_title="DKK/kWh incl. VAT",
            height=360,
        )
    )
    fig.update_xaxes(tickformat="%H:%M")
    return fig


def kpi_tiles():
    k = queries.kpi_summary().iloc[0]
    price_delta, price_class = _pct_delta(k.price_now, k.price_prev,
                                          down_is_good=True, vs="prior day")
    co2_delta, co2_class = _pct_delta(k.co2_now, k.co2_prev,
                                      down_is_good=True, vs="prior day")
    neg_diff = int(k.neg_last30 - k.neg_prev30)
    return kpi_row(
        [
            stat_tile(f"DK1 avg price · {k.price_day:%-d %b}",
                      f"{k.price_now:.2f} DKK/kWh", price_delta, price_class),
            stat_tile(f"DK1 CO₂ intensity · {k.co2_day:%-d %b}",
                      f"{k.co2_now:.0f} g/kWh", co2_delta, co2_class),
            stat_tile("DK1 negative-price hours · 30 days",
                      f"{int(k.neg_last30)} h",
                      f"{neg_diff:+d} vs previous 30 days"),
            stat_tile("Rows ingested · 4 hypertables",
                      f"≈{k.total_rows / 1e6:.1f}M", "updated nightly, 21:45 CPH"),
        ]
    )


def _pct_delta(now, prev, down_is_good: bool, vs: str):
    """Signed % change with color = direction x whether down is the good way."""
    if prev is None or pd.isna(prev) or prev == 0:
        return None, "delta-neutral"
    pct = (now - prev) / abs(prev) * 100
    if abs(pct) < 0.5:
        return f"unchanged vs {vs}", "delta-neutral"
    arrow, good = ("▲", not down_is_good) if pct > 0 else ("▼", down_is_good)
    return (f"{arrow} {abs(pct):.0f}% vs {vs}",
            "delta-good" if good else "delta-bad")


def daily_trend_figure(range_name: str):
    df = queries.daily_prices()
    if range_name == "Last 12 months":
        df = df[df["day"] >= df["day"].max() - pd.DateOffset(months=12)]
    elif range_name == "2022 crisis":
        df = df[(df["day"] >= pd.Timestamp("2021-09-01", tz="UTC"))
                & (df["day"] < pd.Timestamp("2023-07-01", tz="UTC"))]
    fig = go.Figure()
    for area, color in AREA_COLORS.items():
        sub = df[df["price_area"] == area]
        smoothed = sub["avg_price"].rolling(7, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=sub["day"], y=smoothed, name=area,
                mode="lines", line=dict(color=color, width=1.5),
            )
        )
    fig.update_layout(
        base_layout(
            title="Daily average price (7-day average)",
            yaxis_title="DKK/kWh incl. VAT",
            height=420,
        )
    )
    return fig


def heatmap_figure():
    df = queries.price_heatmap_dk1()
    grid = df.pivot(index="hour_of_day", columns="month", values="avg_price")
    fig = go.Figure(
        go.Heatmap(
            x=grid.columns, y=grid.index, z=grid.values,
            colorscale=SEQUENTIAL_BLUES,
            colorbar=dict(title="DKK/kWh", outlinewidth=0),
            hovertemplate="%{x|%b %Y}, %{y}:00 — %{z:.2f} DKK/kWh<extra></extra>",
        )
    )
    fig.update_layout(
        base_layout(
            title="DK1 price by hour of day (Danish time)",
            yaxis_title="Hour of day",
            height=460,
            hovermode="closest",
        )
    )
    fig.update_yaxes(dtick=4)
    return fig
