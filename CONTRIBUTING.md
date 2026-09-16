# Contributing

Open an issue for a large change. A small fix can go straight into a pull request with the reproduction, the resulting behavior, and how you validated it.

```sh
cd desktop
python3 -m unittest discover -s . -p 'test_*.py'
```

Tests must not move a developer’s pointer, type into their Hyprland session, read their clipboard, or capture their monitors. Mock `hyprctl`. Use a named bench if a real GUI is required, never workspaces 1–5.

Do not commit:

- local Hyprland monitor serials
- session directories (`desktop/sessions/`)
- Chromium profiles, cookies, `.keep` from a live machine
- absolute home paths (`/home/you/…`)

Keep install prefixes parameterized (`Path.home()`, `XDG_*`, `@PREFIX@`). English is the default for docs; `README.pt-BR.md` should stay in sync when you change the install story.

By contributing you agree to license the work under MIT. You retain copyright.
