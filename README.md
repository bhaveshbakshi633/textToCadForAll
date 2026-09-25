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

## Status

Early. The repo is being set up. Contributions and ideas are welcome.

## License

MIT
