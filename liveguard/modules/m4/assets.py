"""HTML and CSS assets for OBS browser sources.

Defines inline HTML templates for:
- OverlayTimer: Digital clock with 1-second refresh.
- LiveBadge: High-visibility blinking 'LIVE' badge (~600ms pulse).
- AIDisclosure: Mandatory compliance banner from configuration.
"""

from __future__ import annotations

import base64
import html

OVERLAY_TIMER_HTML: str = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  body {
    margin: 0;
    padding: 0;
    background: transparent;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .timer-card {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(18, 18, 24, 0.75);
    border: 1px solid rgba(255, 255, 255, 0.15);
    backdrop-filter: blur(8px);
    border-radius: 8px;
    padding: 6px 14px;
    color: #ffffff;
    font-size: 20px;
    font-weight: 600;
    letter-spacing: 1px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  }
</style>
</head>
<body>
  <div class="timer-card">
    <span id="clock">00:00:00</span>
  </div>
  <script>
    function updateClock() {
      const d = new Date();
      const pad = (n) => String(n).padStart(2, '0');
      document.getElementById('clock').textContent =
        pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }
    updateClock();
    setInterval(updateClock, 1000);
  </script>
</body>
</html>"""


LIVE_BADGE_HTML: str = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  body {
    margin: 0;
    padding: 0;
    background: transparent;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .live-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(220, 20, 60, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.3);
    border-radius: 6px;
    padding: 4px 12px;
    color: #ffffff;
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    box-shadow: 0 2px 8px rgba(220, 20, 60, 0.5);
  }
  .pulse-dot {
    width: 10px;
    height: 10px;
    background-color: #ffffff;
    border-radius: 50%;
    animation: blink 600ms infinite ease-in-out;
  }
  @keyframes blink {
    0%, 100% { opacity: 1.0; transform: scale(1); }
    50% { opacity: 0.2; transform: scale(0.85); }
  }
</style>
</head>
<body>
  <div class="live-badge">
    <div class="pulse-dot"></div>
    <span>LIVE</span>
  </div>
</body>
</html>"""


AI_DISCLOSURE_BANNER_HTML_TEMPLATE: str = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  body {{
    margin: 0;
    padding: 0;
    background: transparent;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .disclosure-banner {{
    display: flex;
    align-items: center;
    gap: 10px;
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid rgba(56, 189, 248, 0.4);
    border-radius: 8px;
    padding: 8px 16px;
    color: #f8fafc;
    font-size: 14px;
    font-weight: 500;
    line-height: 1.4;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6);
  }}
  .badge {{
    background: #0284c7;
    color: #ffffff;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
</style>
</head>
<body>
  <div class="disclosure-banner">
    <span class="badge">AI Transparency</span>
    <span>{text}</span>
  </div>
</body>
</html>"""


def get_timer_data_uri() -> str:
    """Return data URI for the inline HTML clock browser source."""
    encoded = base64.b64encode(OVERLAY_TIMER_HTML.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"


def get_live_badge_data_uri() -> str:
    """Return data URI for the blinking LIVE badge browser source."""
    encoded = base64.b64encode(LIVE_BADGE_HTML.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"


def render_disclosure_banner_html(text: str) -> str:
    """Render HTML string for the AI disclosure compliance banner.

    Args:
        text: Compliance disclaimer text to render.

    Returns:
        Rendered HTML document string.
    """
    safe_text = html.escape(text)
    return AI_DISCLOSURE_BANNER_HTML_TEMPLATE.format(text=safe_text)


def get_disclosure_banner_data_uri(text: str) -> str:
    """Return data URI for the AI disclosure compliance banner.

    Args:
        text: Compliance disclaimer text to render.

    Returns:
        A valid ``data:text/html;base64,...`` URI.
    """
    content = render_disclosure_banner_html(text)
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"
