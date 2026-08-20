"""Identidad visual de QQ Quant.

El logo se define como SVG en código, no como fichero de imagen, por dos
razones: se adapta al tamaño sin pérdida, y sus colores pueden cambiar con el
tema sin mantener dos archivos.
"""

from __future__ import annotations

VERDE = "#2f9e79"
AZUL = "#4a6fa5"
PLATA = "#c8ccd2"
CLARO = "#f0efec"
FONDO = "#0a1220"
PANEL = "#111c2e"
BORDE = "#1e2d42"
GRIS = "#8c98a8"
ROJO = "#e05252"


def _incrustar(nombre: str, alto: int, alt: str) -> str:
    """Incrusta una imagen del directorio de recursos en la página.

    Se usa base64 en lugar de servir un archivo estático porque Streamlit no
    expone un directorio público: así el logo viaja con la aplicación sin
    depender de rutas externas ni de la configuración del servidor.
    """
    import base64
    from pathlib import Path as _P

    ruta = _P(__file__).parent / nombre
    if not ruta.exists():
        return ""
    datos = base64.b64encode(ruta.read_bytes()).decode("ascii")
    return (
        f'<img src="data:image/png;base64,{datos}" height="{alto}" '
        f'alt="{alt}" style="display:block;border-radius:4px;" />'
    )


def logo_completo(alto: int = 96) -> str:
    """Logotipo con el nombre y el descriptor incluidos.

    Se usa en la cabecera principal. Como la propia imagen ya contiene el
    texto «QQ QUANT» y el descriptor, no debe acompañarse de un título
    escrito aparte: sería el mismo texto dos veces.
    """
    marcado = _incrustar("logo_completo.png", alto, "QQ Quant")
    return marcado or logo_svg(alto)


def logo_marca(alto: int = 44) -> str:
    """Sólo el símbolo, sin texto. Para espacios estrechos."""
    marcado = _incrustar("logo_marca.png", alto, "QQ Quant")
    return marcado or logo_svg(alto)


def logo_img(height: int = 46) -> str:
    """Compatibilidad con la versión anterior."""
    return logo_marca(height)


def logo_svg(size: int = 42, primary: str = CLARO, accent: str = VERDE) -> str:
    """Marca de QQ Quant: dos Q entrelazadas, la segunda en verde.

    Args:
        size: Lado del cuadro en píxeles.
        primary: Color de la primera Q.
        accent: Color de la segunda Q.
    """
    grosor = max(2.0, size * 0.06)
    return f"""<svg width="{size}" height="{size}" viewBox="0 0 42 42"
     role="img" aria-label="QQ Quant">
  <circle cx="16" cy="21" r="10.5" fill="none" stroke="{primary}" stroke-width="{grosor}"/>
  <line x1="22" y1="27" x2="27" y2="32" stroke="{primary}" stroke-width="{grosor}" stroke-linecap="round"/>
  <circle cx="29" cy="21" r="10.5" fill="none" stroke="{accent}" stroke-width="{grosor}"/>
  <line x1="35" y1="27" x2="40" y2="32" stroke="{accent}" stroke-width="{grosor}" stroke-linecap="round"/>
</svg>"""


def header_html(subtitulo: str, estado: str = "", actualizado: str = "") -> str:
    """Cabecera de la aplicación.

    El logotipo ya incluye el nombre y el descriptor, así que aquí no se
    repite en texto. `subtitulo` se conserva por compatibilidad con las
    llamadas existentes, pero no se muestra.
    """
    derecha = ""
    if estado:
        derecha = f"""
      <div style="text-align:right;">
        <div style="display:inline-flex; align-items:center; gap:7px;
                    font-size:12.5px; color:{VERDE};
                    background:rgba(47,158,121,0.12);
                    padding:5px 12px; border-radius:6px;">
          <span style="width:7px;height:7px;border-radius:50%;background:{VERDE};
                       display:inline-block;"></span>{estado}
        </div>
        <div style="font-size:11.5px; color:{GRIS}; margin-top:7px;
                    font-family:ui-monospace,monospace;">{actualizado}</div>
      </div>"""

    return f"""
<div style="display:flex; justify-content:space-between; align-items:center;
            flex-wrap:wrap; gap:16px; padding:2px 0 20px 0;
            border-bottom:1px solid {BORDE}; margin-bottom:18px;">
  {logo_completo(92)}{derecha}
</div>"""


CSS_TEMA = f"""
<style>
  .stApp {{ background: {FONDO}; }}
  .block-container {{ padding-top: 2rem; max-width: 1500px; }}

  h1, h2, h3, h4, h5, h6 {{ color: {CLARO} !important; }}
  p, span, label, li {{ color: #c9ced3; }}

  [data-testid="stMetricValue"] {{
      font-size: 1.55rem; color: {CLARO};
      font-variant-numeric: tabular-nums;
  }}
  [data-testid="stMetricLabel"] {{
      font-size: 0.68rem; letter-spacing: 0.8px;
      text-transform: uppercase; color: {GRIS};
  }}
  [data-testid="stMetric"] {{
      background: {PANEL}; border: 1px solid {BORDE};
      border-radius: 10px; padding: 14px 16px;
  }}

  section[data-testid="stSidebar"] {{
      background: {PANEL}; border-right: 1px solid {BORDE};
  }}

  .stTabs [data-baseweb="tab-list"] {{
      gap: 4px; border-bottom: 1px solid {BORDE};
  }}
  .stTabs [data-baseweb="tab"] {{
      color: {GRIS}; font-size: 14px; padding: 10px 16px;
      background: transparent;
  }}
  .stTabs [aria-selected="true"] {{ color: {CLARO} !important; }}

  .stDataFrame {{ border: 1px solid {BORDE}; border-radius: 10px; }}

  div[data-testid="stDataFrameResizable"] {{ background: {PANEL}; }}

  .stButton button {{
      background: {PANEL}; color: {CLARO}; border: 1px solid {BORDE};
      border-radius: 8px;
  }}
  .stButton button:hover {{ border-color: {VERDE}; color: {VERDE}; }}
  .stButton button[kind="primary"] {{
      background: {VERDE}; color: #06120d; border: none; font-weight: 600;
  }}

  .stSelectbox div[data-baseweb="select"] > div,
  .stNumberInput input, .stTextInput input, .stDateInput input {{
      background: {PANEL} !important; border-color: {BORDE} !important;
      color: {CLARO} !important;
  }}

  hr {{ border-color: {BORDE}; }}
</style>
"""


def ticker_card(simbolo: str, precio: float, variacion: float, digitos: int = 2) -> str:
    """Tarjeta compacta de cotización, en el estilo de un terminal de mercado."""
    sube = variacion >= 0
    color = VERDE if sube else ROJO
    flecha = "▲" if sube else "▼"
    return f"""
<div style="background:{PANEL}; border:1px solid {BORDE}; border-radius:10px;
            padding:12px 14px;">
  <div style="font-size:11.5px; color:{GRIS}; letter-spacing:0.8px;
              font-family:ui-monospace,monospace;">{simbolo}</div>
  <div style="font-size:19px; font-weight:600; color:{CLARO}; margin-top:5px;
              font-variant-numeric:tabular-nums;">{precio:,.{digitos}f}</div>
  <div style="font-size:12px; color:{color}; margin-top:3px;
              font-variant-numeric:tabular-nums;">{flecha} {abs(variacion):.2f}%</div>
</div>"""
