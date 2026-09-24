from __future__ import annotations

import html
import json
import re
from urllib.parse import urlparse


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
QUALITY_VALUES = {"small", "medium", "large", "hd720", "hd1080"}


def _bool_param(value: str | None, *, default: str = "1") -> str:
    return "0" if str(value or default).strip() == "0" else "1"


def _parent_origin(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "*"
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "*"


def build_youtube_embed_html(
    *,
    video_id: str,
    request_origin: str,
    parent_origin: str = "",
    autoplay: str | None = None,
    mute: str | None = None,
    quality: str | None = None,
) -> str:
    if not VIDEO_ID_RE.match(video_id or ""):
        raise ValueError("invalid YouTube videoId")
    origin = str(request_origin or "").rstrip("/") or "https://www.youtube.com"
    parent = _parent_origin(parent_origin)
    autoplay_value = _bool_param(autoplay, default="1")
    mute_value = _bool_param(mute, default="1")
    quality_value = quality if quality in QUALITY_VALUES else ""
    safe_video_id = json.dumps(video_id)
    safe_origin = json.dumps(origin)
    safe_parent = json.dumps(parent)
    safe_quality = json.dumps(quality_value)
    safe_title = html.escape(video_id)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <title>YouTube embed {safe_title}</title>
  <style>
    html,body,#player{{margin:0;width:100%;height:100%;background:#000;overflow:hidden}}
    #overlay{{position:absolute;inset:0;z-index:10;display:grid;place-items:center;pointer-events:none;background:rgba(0,0,0,.16)}}
    #overlay.hidden{{display:none}}
    #overlay span{{width:58px;height:58px;border-radius:50%;background:rgba(255,255,255,.12);box-shadow:0 0 24px rgba(0,0,0,.55)}}
  </style>
</head>
<body>
  <div id="player"></div>
  <div id="overlay" class="hidden"><span></span></div>
  <script>
    (function () {{
      var parentOrigin = {safe_parent};
      var videoId = {safe_video_id};
      var origin = {safe_origin};
      var quality = {safe_quality};
      var started = false;
      var player = null;
      var muteSyncId = null;
      var retryTimers = [];
      var overlay = document.getElementById('overlay');
      function post(message) {{ try {{ window.parent.postMessage(message, parentOrigin); }} catch (err) {{}} }}
      function hideOverlay() {{ overlay.classList.add('hidden'); }}
      function showOverlay() {{ if (!started) overlay.classList.remove('hidden'); }}
      function readMuted() {{
        if (!player) return null;
        if (typeof player.isMuted === 'function') return player.isMuted();
        if (typeof player.getVolume === 'function') return player.getVolume() === 0;
        return null;
      }}
      function stopMuteSync() {{
        if (muteSyncId) window.clearInterval(muteSyncId);
        muteSyncId = null;
      }}
      function startMuteSync() {{
        if (muteSyncId) return;
        var last = readMuted();
        if (last !== null) post({{ type: 'yt-mute-state', muted: last, videoId: videoId }});
        muteSyncId = window.setInterval(function () {{
          var next = readMuted();
          if (next !== null && next !== last) {{
            last = next;
            post({{ type: 'yt-mute-state', muted: next, videoId: videoId }});
          }}
        }}, 600);
      }}
      function tryAutoplay() {{
        if (!player || !player.playVideo) return;
        try {{
          if ({mute_value} === 1 && player.mute) player.mute();
          player.playVideo();
        }} catch (err) {{}}
      }}
      function loadApi() {{
        var tag = document.createElement('script');
        tag.src = 'https://www.youtube.com/iframe_api';
        tag.onerror = function () {{ post({{ type: 'yt-error', code: 'api-load', videoId: videoId }}); }};
        document.head.appendChild(tag);
      }}
      window.onYouTubeIframeAPIReady = function () {{
        player = new YT.Player('player', {{
          videoId: videoId,
          host: 'https://www.youtube.com',
          playerVars: {{
            autoplay: {autoplay_value},
            mute: {mute_value},
            playsinline: 1,
            rel: 0,
            controls: 1,
            modestbranding: 1,
            enablejsapi: 1,
            origin: origin,
            widget_referrer: origin
          }},
          events: {{
            onReady: function () {{
              post({{ type: 'yt-ready', videoId: videoId }});
              if (quality && player.setPlaybackQuality) player.setPlaybackQuality(quality);
              if ({autoplay_value} === 1) {{
                tryAutoplay();
                retryTimers.push(window.setTimeout(function () {{ if (!started) tryAutoplay(); }}, 600));
                retryTimers.push(window.setTimeout(function () {{ if (!started) tryAutoplay(); }}, 1600));
                retryTimers.push(window.setTimeout(function () {{
                  if (!started) post({{ type: 'yt-autoplay-failed', videoId: videoId }});
                }}, 2800));
              }}
              startMuteSync();
            }},
            onError: function (event) {{
              stopMuteSync();
              post({{ type: 'yt-error', code: event && event.data, videoId: videoId }});
            }},
            onStateChange: function (event) {{
              post({{ type: 'yt-state', state: event && event.data, videoId: videoId }});
              if (event && (event.data === 1 || event.data === 3)) {{
                started = true;
                hideOverlay();
                retryTimers.forEach(window.clearTimeout);
                retryTimers = [];
              }}
            }}
          }}
        }});
      }};
      window.addEventListener('message', function (event) {{
        if (!player || !event.data || !event.data.type) return;
        switch (event.data.type) {{
          case 'play': if (player.playVideo) player.playVideo(); break;
          case 'pause': if (player.pauseVideo) player.pauseVideo(); break;
          case 'mute': if (player.mute) player.mute(); break;
          case 'unmute': if (player.unMute) player.unMute(); break;
          case 'setQuality': if (event.data.quality && player.setPlaybackQuality) player.setPlaybackQuality(event.data.quality); break;
        }}
      }});
      window.setTimeout(showOverlay, 4000);
      window.setTimeout(function () {{
        if (!started) post({{ type: 'yt-timeout', videoId: videoId }});
      }}, 12000);
      window.addEventListener('beforeunload', function () {{
        stopMuteSync();
        retryTimers.forEach(window.clearTimeout);
      }});
      if (document.requestStorageAccess) document.requestStorageAccess().catch(function () {{}});
      loadApi();
    }})();
  </script>
</body>
</html>"""


def youtube_embed_headers() -> dict[str, str]:
    return {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Permissions-Policy": 'autoplay=*, encrypted-media=*, storage-access=(self "https://www.youtube.com")',
        "X-Content-Type-Options": "nosniff",
    }
