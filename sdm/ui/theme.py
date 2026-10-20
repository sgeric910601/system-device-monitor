"""介面配色與 GTK CSS；不載入 GTK，方便測試與重用。"""

COLORS = {"cpu": "#64a8ff", "gpu": "#bc91ff", "ram": "#55c98c", "network": "#46cbd2",
          "temperature": "#ffa467", "ups": "#e2c45b"}

# 曲線圖使用的 RGB（0–1），與 COLORS 同色系。
GRAPH_COLORS = {"cpu": (.39, .66, 1), "gpu": (.74, .57, 1), "ram": (.33, .79, .55)}

BASE_CSS = """
        window { font-family: "Noto Sans CJK TC", "Ubuntu", sans-serif; }
        .page { padding: 22px; }
        .card { border-radius: 14px; border: 1px solid alpha(@theme_fg_color, .12);
                background-color: alpha(@theme_fg_color, .025); padding: 16px; }
        .value { font-size: 29px; font-weight: 700; }
        .muted { color: alpha(@theme_fg_color, .66); font-size: 12px; }
        .section { font-size: 17px; font-weight: 700; margin-top: 8px; }
        .device-name { font-size: 15px; font-weight: 600; }
        .tag { border-radius: 8px; padding: 4px 8px; background-color: alpha(@theme_fg_color, .07); }
        .device-row { padding: 13px; border-bottom: 1px solid alpha(@theme_fg_color, .09); }
        .device-power { font-size: 17px; font-weight: 600; }
        .footnote { padding: 8px 22px; font-size: 11px; color: alpha(@theme_fg_color, .6); }
        """


def accent_css(key, color):
    """單一強調色的卡片、數值與進度條樣式。"""
    return (".accent-" + key + " { border-color: alpha(" + color + ", .42); "
            "background-color: alpha(" + color + ", .065); border-top-width: 3px; } "
            ".accent-" + key + " .value, .accent-" + key + " .device-name, .accent-" + key +
            " .device-power { color: " + color + "; } "
            ".accent-" + key + " progress { background-color: " + color +
            "; background-image: none; }")


def css_data(colors=None):
    """組合完整 CSS，回傳可交給 Gtk.CssProvider 的位元組。"""
    palette = COLORS if colors is None else colors
    return (BASE_CSS + "\n".join(accent_css(key, color)
                                 for key, color in palette.items())).encode()
