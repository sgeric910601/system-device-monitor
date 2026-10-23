"""介面配色與 GTK CSS；不載入 GTK，方便測試與重用。"""

COLORS = {"cpu": "#64a8ff", "gpu": "#bc91ff", "ram": "#55c98c", "network": "#46cbd2",
          "temperature": "#ffa467", "ups": "#e2c45b"}

# 曲線圖使用的 RGB（0–1），與 COLORS 同色系。
GRAPH_COLORS = {"cpu": (.39, .66, 1), "gpu": (.74, .57, 1), "ram": (.33, .79, .55)}

# 改由不斷電系統電池供電（警告）與電池電量低（嚴重）的文字顏色，明暗主題都看得清楚。
WARNING = "#e8913a"
CRITICAL = "#e5534b"
GOOD = "#3fb27f"                # 健康度良好、記憶體壓力正常
HEX_DIGITS = 6
# 充電夥伴與桌面寵物：休息時的 Z、充電火花、眼窩與流汗，在淺色與深色桌布上都看得清楚
COMPANION = {"zzz": "#7f97c8", "spark": "#ffc83d", "socket": "#333d4d", "sweat": "#6cb6e8",
             "lid_line": "#211f24", "bubble": "#fbfbfa", "bubble_text": "#222222",
             "heart": "#ff5c8a"}

# 系統監控頁 2 分鐘曲線的顏色（第一條線、第二條線）
SERIES = {"cpu": COLORS["cpu"], "gpu": COLORS["gpu"], "memory": COLORS["ram"],
          "read": COLORS["ram"], "write": COLORS["temperature"],
          "down": COLORS["network"], "up": COLORS["ups"]}
# 記憶體組成長條的顏色（RGBA）；快取與空閒用前景色的透明度，明暗主題都適用
SEGMENT_HEX = (("app", COLORS["cpu"] + "ff"), ("shared", COLORS["gpu"] + "ff"),
               ("kernel", COLORS["temperature"] + "ff"), ("cache", "#8c8c8cb3"),
               ("other", COLORS["ups"] + "ff"), ("free", "#8c8c8c40"))

BASE_CSS = """
        window { font-family: "Noto Sans CJK TC", "Ubuntu", sans-serif; }
        .page { padding: 22px; }
        .narrow .page { padding: 12px; }
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
        .power-value { font-size: 44px; font-weight: 700; }
        .power-sub { font-size: 13px; color: alpha(@theme_fg_color, .72); }
        .power-state { font-size: 15px; font-weight: 600; }
        .power-headline { font-size: 17px; font-weight: 600; }
        .flow-value { font-size: 15px; font-weight: 600; }
        .metric { border-radius: 9px; padding: 8px 10px;
                  background-color: alpha(@theme_fg_color, .05); }
        .metric-value { font-size: 16px; font-weight: 600; }
        .monitor-value { font-size: 34px; font-weight: 700; }
        .badge { border-radius: 10px; padding: 2px 10px; font-weight: 600; font-size: 12px;
                 background-color: alpha(@theme_fg_color, .08); }
        .swatch { border-radius: 3px; min-width: 10px; min-height: 10px; }
        .volume-bar trough, .volume-bar progress { min-height: 6px; }
        """ + f"""
        .power-warning {{ color: {WARNING}; }}
        .power-critical {{ color: {CRITICAL}; font-weight: 600; }}
        .tone-good {{ color: {GOOD}; }}
        .tone-warning {{ color: {WARNING}; }}
        .tone-critical {{ color: {CRITICAL}; }}
        .badge.tone-good {{ background-color: alpha({GOOD}, .16); }}
        .badge.tone-warning {{ background-color: alpha({WARNING}, .18); }}
        .badge.tone-critical {{ background-color: alpha({CRITICAL}, .18); }}
        .bar-warning progress {{ background-color: {WARNING}; border-color: {WARNING};
                                 background-image: none; }}
        .bar-critical progress {{ background-color: {CRITICAL}; border-color: {CRITICAL};
                                  background-image: none; }}
        """


def rgba(color):
    """「#rrggbbaa」或「#rrggbb」轉成 0–1 的 (r, g, b, a)。"""
    digits = color[1:] if color.startswith("#") else ""
    if len(digits) == HEX_DIGITS + 2:
        alpha = int(digits[HEX_DIGITS:], 16) / 255
        return rgb("#" + digits[:HEX_DIGITS]) + (alpha,)
    return rgb(color) + (1.0,)


def rgb(color):
    """「#rrggbb」轉成 0–1 的 (r, g, b)，給 Cairo 繪圖使用。"""
    digits = color[1:] if color.startswith("#") else ""
    if len(digits) != HEX_DIGITS:
        raise ValueError("Expected #rrggbb")
    try:
        return tuple(int(digits[i:i + 2], 16) / 255 for i in range(0, HEX_DIGITS, 2))
    except ValueError:
        raise ValueError("Expected #rrggbb") from None


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


SEGMENTS = tuple((key, rgba(color)) for key, color in SEGMENT_HEX)
