"""Chart theme: palette tokens + a shared plotly layout.

Colors follow the entity, never the rank (fixed slots):
  slot 1 blue  -> DK1 / onshore wind / primary single-series
  slot 2 aqua  -> DK2 / offshore wind
  slot 3 yellow-> solar
Sequential (heatmaps) = one blue hue, light->dark. No dual axes anywhere.
"""

# Categorical slots (light surface, CVD-validated order — never cycle past these)
BLUE = "#2a78d6"
AQUA = "#1baf7a"
YELLOW = "#eda100"
GREEN = "#008300"

AREA_COLORS = {"DK1": BLUE, "DK2": AQUA}

# Single-hue sequential ramp for heatmaps (blue 100 -> 700)
SEQUENTIAL_BLUES = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]

# Chrome & ink
SURFACE = "#fcfcfb"
PAGE_BG = "#f9f9f7"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
AXIS_LINE = "#c3c2b7"

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def base_layout(**overrides) -> dict:
    """Shared plotly layout: recessive hairline grid, quiet axes, system sans."""
    layout = dict(
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT_FAMILY, color=INK_SECONDARY, size=13),
        title_font=dict(color=INK_PRIMARY, size=16),
        margin=dict(l=56, r=24, t=56, b=48),
        hovermode="x unified",
        xaxis=dict(gridcolor=GRIDLINE, linecolor=AXIS_LINE, zeroline=False,
                   tickfont=dict(color=INK_MUTED)),
        yaxis=dict(gridcolor=GRIDLINE, linecolor=AXIS_LINE, zeroline=False,
                   tickfont=dict(color=INK_MUTED)),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0,
                    font=dict(color=INK_SECONDARY)),
    )
    layout.update(overrides)
    return layout
