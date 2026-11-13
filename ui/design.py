"""Display-only brand tokens; never imported by financial/hash code."""

BRAND = "SIGFIN · 시그핀"
NAVY = "#0F172A"
BLUE = "#1D4ED8"
MUTED = "#475569"
BORDER = "#E2E8F0"
FONT = '"Malgun Gothic", "Apple SD Gothic Neo", "Segoe UI", sans-serif'


def style_figure(figure):
    """Only presentation changes; numerical traces/UTC/uirevision survive."""
    figure.update_layout(font=dict(family=FONT, color=NAVY),
                         paper_bgcolor="white", plot_bgcolor="white")
    return figure
