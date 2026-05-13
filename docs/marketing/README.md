# Marketing assets

Promo materials for sharing the project (微信 / Twitter / blog).

## Files

| File | Purpose |
|---|---|
| `promo-cn.html` | Chinese promo poster for 微信 sharing — open in browser, screenshot for sharing. |

## How to produce a shareable PNG

1. Open `promo-cn.html` in any browser. QR code loads from `api.qrserver.com` (no
   local dependency needed).
2. Screenshot the rendered poster (Cmd+Shift+4 on macOS, area-select; or use
   browser dev-tools full-page screenshot).
3. Recommended export width for WeChat group sharing: 720-1080 px wide.

## Style notes

- Color palette is "night-coding vibe": warm amber (`#ffa940`) + cyan (`#5eead4`)
  on deep purple-black (`#0d0a1e`). Chosen to feel distinct from typical
  enterprise-blue OSS promo material.
- Typography defaults to system Chinese sans (PingFang SC on macOS, Microsoft
  YaHei on Windows). No font embedding — works offline once rendered.
- The QR code points to the GitHub repo. To repoint, change the `data=` URL in
  the `<img src="...qrserver.com..." />` tag near the bottom.

## Updating the feedback solicitation

The poster invites feedback on which CLI tools to add internal support for
next. The candidate list lives in the `<div class="feedback">` block of
`promo-cn.html` — that's the single source of truth. When a CLI lands as a
built-in preset, move it from there into the subtitle's "已支持" list.
