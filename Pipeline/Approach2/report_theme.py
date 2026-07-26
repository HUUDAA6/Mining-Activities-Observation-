from __future__ import annotations

import json
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    Paragraph, Spacer, Table, TableStyle, LongTable, HRFlowable,
    Image as RLImage,
)


_THEME_DIR = os.path.dirname(os.path.abspath(__file__))
_STYLE_PATH = os.path.join(_THEME_DIR, "report_style.json")

with open(_STYLE_PATH, "r", encoding="utf-8") as _f:
    STYLE = json.load(_f)

_C = STYLE["colors"]
_S = STYLE["spacing"]


PRIMARY    = colors.HexColor(_C["primary"])
SECONDARY  = colors.HexColor(_C["secondary"])
ACCENT     = colors.HexColor(_C["accent"])
BG_LIGHT   = colors.HexColor(_C["bg_light"])
TEXT_MAIN  = colors.HexColor(_C["text_main"])
TEXT_MUTED = colors.HexColor(_C["text_muted"])
SUCCESS    = colors.HexColor(_C["success"])
DANGER     = colors.HexColor(_C["danger"])
WARNING    = colors.HexColor(_C["warning"])
WHITE      = colors.HexColor(_C["white"])


HEX = {
    "primary":    _C["primary"],
    "secondary":  _C["secondary"],
    "accent":     _C["accent"],
    "bg_light":   _C["bg_light"],
    "text_main":  _C["text_main"],
    "text_muted": _C["text_muted"],
    "success":    _C["success"],
    "danger":     _C["danger"],
    "warning":    _C["warning"],
    "white":      _C["white"],
}


PAD_V   = int(_S["table_pad_v"])
PAD_H   = int(_S["table_pad_h"])
KPI_RH  = list(_S["kpi_row_heights"])   # [50, 20]
GRID_W  = 0.5

W_PAGE  = A4[0] - 36 * mm

LOGO_PATH = os.path.join(_THEME_DIR, "Templates", "logo.png")

def _style(name, **kw):
    base = ParagraphStyle(name)
    for k, v in kw.items():
        setattr(base, k, v)
    return base


ST_TITLE    = _style("t",  fontSize=18, textColor=PRIMARY, fontName="Helvetica-Bold",
                      alignment=TA_CENTER, leading=22)
ST_SUBTITLE = _style("su", fontSize=10, textColor=TEXT_MUTED, fontName="Helvetica",
                      alignment=TA_CENTER, leading=13)
ST_SECTION  = _style("se", fontSize=12, textColor=WHITE, fontName="Helvetica-Bold",
                      alignment=TA_LEFT, leading=14)
ST_LABEL    = _style("lb", fontSize=9,  textColor=TEXT_MAIN, fontName="Helvetica-Bold")
ST_VALUE    = _style("vl", fontSize=9,  textColor=TEXT_MAIN, fontName="Helvetica")
ST_VAL_POS  = _style("vp", fontSize=9,  textColor=SUCCESS,   fontName="Helvetica-Bold")
ST_VAL_NEG  = _style("vn", fontSize=9,  textColor=DANGER,    fontName="Helvetica-Bold")
ST_VAL_WARN = _style("vw", fontSize=9,  textColor=WARNING,   fontName="Helvetica-Bold")
ST_FOOTER   = _style("ft", fontSize=7.5, textColor=TEXT_MUTED, fontName="Helvetica",
                      alignment=TA_CENTER)
ST_CAPTION  = _style("cp", fontSize=8.5, textColor=TEXT_MUTED, fontName="Helvetica-Oblique",
                      alignment=TA_CENTER, leading=11)
ST_KPI_NUM  = _style("kn", fontSize=16, textColor=PRIMARY, fontName="Helvetica-Bold",
                      alignment=TA_CENTER, leading=19)
ST_KPI_LBL  = _style("kl", fontSize=8,  textColor=TEXT_MUTED, fontName="Helvetica",
                      alignment=TA_CENTER, leading=10)



def base_table_style():
    """Token list — feed to TableStyle(base_table_style() + [extras])."""
    return [
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, BG_LIGHT]),
        ("GRID",           (0, 0), (-1, -1), GRID_W, SECONDARY),
        ("BOX",            (0, 0), (-1, -1), GRID_W, SECONDARY),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",    (0, 0), (-1, -1), PAD_H),
        ("RIGHTPADDING",   (0, 0), (-1, -1), PAD_H),
        ("TOPPADDING",     (0, 0), (-1, -1), PAD_V),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), PAD_V),
    ]


def section_major(text):
    """Primary (dark) section header — key blocks."""
    t = Table([[Paragraph(text.strip().upper(), ST_SECTION)]], colWidths=[W_PAGE])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), PRIMARY),
        ("TOPPADDING",    (0, 0), (-1, -1), PAD_V),
        ("BOTTOMPADDING", (0, 0), (-1, -1), PAD_V),
        ("LEFTPADDING",   (0, 0), (-1, -1), PAD_H + 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), PAD_H),
    ]))
    return t


def section_minor(text):
    """Secondary (mid-tone) section header — sub-blocks."""
    t = Table([[Paragraph(text.strip().upper(), ST_SECTION)]], colWidths=[W_PAGE])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), SECONDARY),
        ("TOPPADDING",    (0, 0), (-1, -1), PAD_V - 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), PAD_V - 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), PAD_H + 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), PAD_H),
    ]))
    return t

def header_strip(title_text, subtitle_text):
    """Centered logo → title → date → accent rule."""
    items = []
    if os.path.exists(LOGO_PATH):
        logo = RLImage(LOGO_PATH, width=26 * mm, height=26 * mm)
        logo.hAlign = "CENTER"
        items.append(logo)
        items.append(Spacer(1, 3 * mm))
    items.append(Paragraph(title_text, ST_TITLE))
    items.append(Spacer(1, 2 * mm))
    items.append(Paragraph(subtitle_text, ST_SUBTITLE))
    items.append(Spacer(1, 4 * mm))
    items.append(HRFlowable(width="100%", thickness=1.5, color=ACCENT,
                            lineCap="round", spaceBefore=0, spaceAfter=0))
    items.append(Spacer(1, 6 * mm))
    return items


def info_table(rows):
    """Two-column label/value table. rows = [(label, value[, style]), ...]."""
    data = []
    for row in rows:
        lbl, val = row[0], row[1]
        vstyle = row[2] if len(row) > 2 else ST_VALUE
        data.append([Paragraph(lbl, ST_LABEL), Paragraph(str(val), vstyle)])
    t = LongTable(data, colWidths=[68 * mm, W_PAGE - 68 * mm])
    t.setStyle(TableStyle(base_table_style()))
    return t


def kpi_card(value_text, label_text, val_style=None):
    """Uniform KPI card — fixed row heights from JSON spacing."""
    vs = val_style or ST_KPI_NUM
    t = Table(
        [[Paragraph(value_text, vs)],
         [Paragraph(label_text, ST_KPI_LBL)]],
        colWidths=[W_PAGE / 4 - 2 * mm],
        rowHeights=KPI_RH,
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), BG_LIGHT),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BOX",           (0, 0), (-1, -1), 0.8, ACCENT),
    ]))
    return t


def kpi_row(cards):
    t = Table([cards], colWidths=[W_PAGE / len(cards)] * len(cards))
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def img_block(png_path, caption, ratio=1.0):
    if not os.path.exists(png_path):
        return Spacer(1, 1)
    img_w = W_PAGE * 0.97
    img_h = img_w * ratio
    if img_h > 160 * mm:
        img_h = 160 * mm
        img_w = img_h / ratio
    t = Table(
        [[RLImage(png_path, width=img_w, height=img_h)],
         [Paragraph(caption, ST_CAPTION)]],
        colWidths=[W_PAGE],
    )
    t.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0.8, SECONDARY),
        ("LINEBELOW",     (0, 0), (-1, 0), GRID_W, SECONDARY),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND",    (0, 1), (-1, 1), BG_LIGHT),
        ("TOPPADDING",    (0, 0), (-1, -1), PAD_V - 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), PAD_V - 2),
    ]))
    return t

def footer(text):
    return [
        HRFlowable(width="100%", thickness=0.8, color=ACCENT),
        Spacer(1, 2 * mm),
        Paragraph(text, ST_FOOTER),
        Spacer(1, 2 * mm),
        Paragraph("Presented by : Houda El Barehoumi", ST_FOOTER),
    ]



def fmt_vol_clean(v):
    """Billion / Million / k / raw m³ formatter."""
    if v is None:
        return "N/A"
    av = abs(v)
    if av >= 1e9:
        return f"{v / 1e9:+.2f} Billion m³"
    if av >= 1e6:
        return f"{v / 1e6:+.2f} Million m³"
    if av >= 1e3:
        return f"{v / 1e3:+.1f}k m³"
    return f"{int(v):+,} m³"


def val_style_auto(val, invert=False):
    try:
        num = float(str(val).replace(",", "").replace("+", "")
                    .replace(" m³", "").replace(" ha", "").replace(" m", "").replace("%", ""))
        if num > 0:
            return ST_VAL_POS if not invert else ST_VAL_NEG
        if num < 0:
            return ST_VAL_NEG if not invert else ST_VAL_POS
    except ValueError:
        pass
    return ST_VALUE


def apply_matplotlib_defaults():
    """Call once per plotting script to pick up the shared palette."""
    import matplotlib as mpl
    mpl.rcParams.update({
        "font.family":       "DejaVu Sans",
        "axes.edgecolor":    HEX["secondary"],
        "axes.labelcolor":   HEX["text_main"],
        "axes.titlecolor":   HEX["primary"],
        "axes.titleweight":  "bold",
        "xtick.color":       HEX["text_muted"],
        "ytick.color":       HEX["text_muted"],
        "text.color":        HEX["text_main"],
        "grid.color":        HEX["bg_light"],
        "savefig.facecolor": HEX["white"],
        "figure.facecolor":  HEX["white"],
    })
