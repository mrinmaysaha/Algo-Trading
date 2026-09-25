# OpenAlgo Frontend Design System

## Design Philosophy
OpenAlgo adopts an institutional, dense, dark-mode-first aesthetic inspired by professional financial terminals (Bloomberg, TradingView, Delta Exchange). Clarity, high information density, low eye strain, and millisecond-level responsiveness guide all interface surfaces.

---

## 1. Color Tokens & Theme System

```css
:root {
  /* Surface & Background */
  --bg-primary: #090d16;       /* Deep obsidian canvas */
  --bg-surface: #111827;       /* Card and table panel background */
  --bg-surface-hover: #1f2937; /* Elevated card hover */
  --border-subtle: #1f293d;    /* Subtle division border */
  --border-active: #374151;    /* Active focus border */

  /* Market Action Palette */
  --color-bull: #10b981;       /* Emerald Green: Long / Profit / Uptrend */
  --color-bull-glow: rgba(16, 185, 129, 0.15);
  --color-bear: #ef4444;       /* Crimson Red: Short / Loss / Downtrend */
  --color-bear-glow: rgba(239, 68, 68, 0.15);
  --color-neutral: #6b7280;    /* Muted Slate: Flat / Inactive / Disabled */

  /* Accent & Highlights */
  --accent-cyan: #06b6d4;      /* Terminal prompt, active navigation tabs */
  --accent-indigo: #6366f1;    /* Primary CTA buttons, brand badges */
  --accent-amber: #f59e0b;     /* Warnings, partial fills, pending state */

  /* Typography */
  --text-primary: #f9fafb;     /* High contrast body and titles */
  --text-secondary: #9ca3af;   /* Metadata labels, timestamp captions */
  --text-muted: #6b7280;       /* Disabled text, table headers */
}
```

---

## 2. Typography Hierarchy

- **Primary Font**: `Inter`, `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `Roboto`, sans-serif.
- **Monospace / Numerical Data**: `JetBrains Mono`, `Fira Code`, `Consolas`, monospace. All prices, PnL numbers, strike rates, and quantities **must** use monospace with tabular figures (`font-variant-numeric: tabular-nums`) to prevent layout shifting during real-time streaming updates.

| Element | Size | Weight | Font Family |
|---|---|---|---|
| **Header 1 (Page Title)** | `1.5rem (24px)` | Semi-Bold (600) | Sans-serif |
| **Section Header** | `1.125rem (18px)` | Medium (500) | Sans-serif |
| **Card / Widget Label** | `0.875rem (14px)` | Medium (500) | Sans-serif |
| **Body Text** | `0.875rem (14px)` | Regular (400) | Sans-serif |
| **Numeric Value / Metric** | `1.25rem (20px)` | Bold (700) | Monospace |
| **Table Data Cell** | `0.8125rem (13px)` | Regular (400) | Monospace / Sans |

---

## 3. Component Inventory & Standards

### Metric KPI Card
- Dark slate surface background (`--bg-surface`).
- 1px border (`--border-subtle`) with `border-radius: 8px`.
- Upper label in muted uppercase text (`0.75rem`, `--text-secondary`).
- Primary numerical value rendered in bold tabular monospace.
- Trend delta badge (Emerald for positive PnL, Crimson for negative PnL).

### Data Tables (Orders, Positions, Logs)
- Dense layout (`padding: 8px 12px` per row).
- Alternating row background or subtle hover highlight (`--bg-surface-hover`).
- Fixed header sticky on vertical scrolling.
- PnL columns right-aligned and color-coded.

### Action Buttons
- **Primary CTA (Place Order / Start Strategy)**: Background `--accent-indigo`, text `--text-primary`, hover brightness 110%, active scale 0.98.
- **Danger / Panic Button (Emergency Exit / Kill All)**: Background `--color-bear`, text `#ffffff`, high visual prominence.
- **Secondary / Ghost Button**: Transparent background, 1px border (`--border-subtle`), text `--text-secondary`.

---

## 4. Interaction Patterns & States

1. **Loading State**: Subtle pulse or spinner indicator. Do not block the entire UI unless performing a blocking authentication handshake.
2. **Order Execution Toast**: Toast notifications auto-dismiss in 4 seconds. Emerald toast for fills, Amber toast for partial fills, Crimson toast with error reason for rejections.
3. **Empty State**: Explicit graphic or scannable message: *"No active positions. Strategies will execute when market conditions are met."*
4. **WebSocket Disconnected Banner**: Top sticky warning banner when live quote WebSocket connection drops, auto-clearing upon reconnection.

---

## 5. UI Rules (Do's & Don'ts)

- **DO** format all monetary amounts and PnL with exact currency symbols (`$`, `₹`) and explicit 2 decimal places.
- **DO** maintain high contrast ratios for readability in dark mode.
- **DON'T** use jarring pure white backgrounds (`#ffffff`) or pure black (`#000000`) surfaces.
- **DON'T** reorder columns or shift DOM elements dynamically during live tick updates.
