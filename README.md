# textToCadForAll

Let anyone turn a plain-language description into a **real, manufacturable CAD design** using the AI
of their choice: Claude, GPT, or any other model that can call tools.

## Why

Today AI can talk about designs but rarely produces parts a workshop can actually make.
This project builds the missing layer between an AI model and a real CAD system (SolidWorks first),
so the model can create, inspect, and fix geometry in real time while a human watches and steers.

## Principles

- **Real designs only.** Every part must be manufacturable and every assembly must be assemblable:
  standard stock, off-the-shelf parts, fasteners with tool access, valid sheet-metal flat patterns.
- **Model-agnostic.** All capabilities are exposed as MCP tools so any AI client can use them.
- **API over simulated input.** Drive CAD through its native API (COM for SolidWorks). Use
  mouse/keyboard UI automation only as a narrow fallback where no API exists.
- **Honest capability tiers.** Every tool is labelled verified, pilot, or blocked. Pilot output needs
  human review; blocked means it does not work yet.
- **See the result.** After each change the AI renders and checks the model visually and numerically.

## Roadmap

1. Live-session MCP server: read the user's current selection, view, feature tree, rebuild errors,
   and screenshots so the AI is aware of what the human is doing.
2. Action tools: sketches, features, mates, dimensions, with undo after every step.
3. UI-automation fallback for dialogs and options with no API.
4. Design checks: interference, clearances, fastener access, sheet-metal rules, DFM.
5. Support for other CAD systems and other AI clients.

## Live-session server (SolidWorks)

`live_session_server.py` is a stdio MCP server that gives any AI client real-time awareness of
a running SOLIDWORKS session. It only reads through the native COM API and never sends
simulated mouse or keyboard input.

| Tool | Tier | What it does |
|---|---|---|
| `live_status` | verified | Version, active document, unsaved/rebuild flags, selection count, in-context edit state |
| `get_selection` | verified | What the human selected: kind, owning component, click point, plane normal / cylinder axis and radius / circle centre / line endpoints, all in mm |
| `get_view` | verified | Rotation matrix, translation and zoom of the active view |
| `get_feature_tree` | verified | Features with type, suppression and error code; assembly components with fixed/suppressed state |
| `get_rebuild_errors` | verified | Only the features carrying errors, plus suppressed components |
| `screenshot` | verified | PNG of the graphics area, optionally from a named view with zoom-to-fit; the human's view is restored afterwards |
| `rebuild` | pilot | Rebuild and report errors |
| `undo` | pilot | Undo N steps (same as Ctrl+Z) to back out a rejected AI action |
| `select_by_name` | pilot | Select a named plane/face/feature/component for a following action |
| `clear_selection` | verified | Clear the selection |

Verified on SOLIDWORKS 2026 SP3, Python 3.14, pywin32 312, mcp 2.2.

### Install

```powershell
pip install -r requirements.txt
claude mcp add --scope user sw-live -- python C:\path\to\live_session_server.py
```

Any MCP client works the same way (Cursor, Codex, Claude Desktop, ...): run
`python live_session_server.py` over stdio. SOLIDWORKS must already be running at the same
privilege level as the server.

## Status

Live-session watch tools work. Action tools (sketches, features, mates) come next.
Contributions and ideas are welcome.

## License

MIT
