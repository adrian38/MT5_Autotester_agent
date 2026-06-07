# Design System — MT5 Autotester

This document is the single source of truth for visual rules. All UI code
must follow these rules. When adding or modifying any widget, pick the right
type from the catalogue below; do not invent new styles.

---

## 1. Button catalogue

### Type A — CTA (Call-To-Action)

Full-width standalone button inside a card. One per section/card.

```python
self._rounded_button_cls(
    parent,
    text="…",
    bg=self.colors["accent"],          # green  – save/execute
    # bg=self.colors["primary_container"]  # dark   – secondary save
    # bg=self.colors["panel"], fg=self.colors["danger"], border=self.colors["danger"]  # destructive
    hover_bg=self.colors["accent_hover"],
    font=("Segoe UI", 10, "bold"),
    radius=12, padx=18, pady=10,
    parent_bg=self.colors["panel"],
    command=…,
)
```

| Variant | bg | Example |
|---|---|---|
| Primary save/execute | `accent` | "Guardar rutas", "Lanzar Agente UBS" |
| Secondary / configure | `primary_container` | "Guardar config" |
| Danger standalone | `panel` + `fg=danger` + `border=danger` | "Eliminar datos historicos" |

### Type B — Compact bar button

Used exclusively inside `panel_alt` action bars (never in card content).

```python
# Standard
tk.Button(bar, text="…",
    bg=self.colors["panel"], fg=self.colors["muted"],
    relief="solid", borderwidth=1, padx=8, pady=5,
    font=("Segoe UI", 9), cursor="hand2", command=…)

# Primary accent (1 per bar max)
tk.Button(bar, text="…",
    bg=self.colors["accent"], fg="#ffffff",
    relief="flat", borderwidth=0, padx=10, pady=5,
    font=("Segoe UI", 9, "bold"), cursor="hand2", command=…)

# Danger
tk.Button(bar, text="…",
    bg=self.colors["danger"], fg="#ffffff",
    relief="flat", borderwidth=0, padx=8, pady=5,
    font=("Segoe UI", 9, "bold"), cursor="hand2", command=…)
```

### Type C — Card content button

Used inside card body (not in a `panel_alt` bar).

```python
ttk.Button(parent, text="…", style="Primary.TButton", command=…)  # accent
ttk.Button(parent, text="…", style="TButton",         command=…)  # neutral
ttk.Button(parent, text="…", style="Danger.TButton",  command=…)  # destructive
ttk.Button(parent, text="…", style="Tool.TButton",    command=…)  # compact utility
```

---

## 2. Action bar pattern

Every `panel_alt` toolbar follows this structure:

```python
bar = tk.Frame(parent, bg=self.colors["panel_alt"])
bar.grid(row=N, column=0, sticky="ew", padx=20, pady=(4, 8))
bar.columnconfigure(0, weight=1)          # summary label fills left
tk.Label(bar, textvariable=…,
    bg=self.colors["panel_alt"], fg=self.colors["muted"],
    font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=6)
# Type-B buttons right-aligned:  padx=(0,6) between, padx=(0,10) for last
```

Multi-row bars: row 0 = global run actions; row 1 = per-row/selection actions.

---

## 3. Input fields

| Widget | Width | Notes |
|---|---|---|
| `ttk.Entry` in form (path row, tester fields) | no width, `sticky="ew"` | fills column |
| `ttk.Entry` date | `width=14` | YYYY.MM.DD |
| `ttk.Entry` numeric criterion | `width=8` | criteria bars |
| `ttk.Entry` read-only display | `width=7`, `state="readonly"` | results criteria bar |
| `ttk.Spinbox` numeric | `width=8` | all spinboxes |
| `ttk.Combobox` standard | `width=12` | dropdowns |
| `ttk.Combobox` period/TF | `width=10` | timeframe pickers |

---

## 4. Treeview standard

Every `ttk.Treeview` must follow ALL four rules:

```python
# 1 — stretch=False on every column (enables horizontal scroll)
tree.column(col, width=w, minwidth=42, anchor="center", stretch=False)

# 2 — always call both helpers
self._make_tree_sortable(tree)
self._attach_tree_scrollbars(parent_frame, tree, row_index)

# 3 — always specify height
ttk.Treeview(parent, columns=…, height=N)
# Use: 6=compact list, 10=medium, 12=tall, 14=editor list, 18=fullscreen

# 4 — always configure standard tags
tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
tree.tag_configure("rejected", foreground=self.colors["danger"])
tree.tag_configure("pending",  foreground=self.colors["muted"])
```

---

## 5. Spacing system

| Context | Value |
|---|---|
| Card outer margin | `padx=20` from card edge |
| Between cards | `pady=(0, 16)` |
| Form row | `pady=7` (label + entry + button) |
| Toolbar bar padding | `pady=(4, 8)` |
| Section header → content | `pady=(16, 6)` |
| Button row bottom | `pady=(0, 18)` or `pady=(14, 22)` |

---

## 6. Typography

| Role | Font |
|---|---|
| Card title | `("Segoe UI", 11, "bold")` |
| Action card title | `("Segoe UI", 10, "bold")` |
| CTA button | `("Segoe UI", 10, "bold")` |
| Bar button standard | `("Segoe UI", 9)` |
| Bar button primary | `("Segoe UI", 9, "bold")` |
| Form label | `("Segoe UI", 10)` via `Panel.TLabel` style |
| Metric value | `("Segoe UI", 26, "bold")` |
| Code / path | `("Consolas", 9)` |

---

## 7. Colour roles

| Token | Role |
|---|---|
| `accent` / `accent_hover` | Positive: save, execute, confirm |
| `primary` / `primary_container` | Navigate, secondary save |
| `danger` / `danger_soft` | Destructive: delete, reset |
| `panel_alt` | Toolbar / info bar backgrounds |
| `muted` | Subtle text, disabled-like labels |
| `border` | Dividers, card outlines |

---

## 8. Prohibited patterns

- **Never** mix `ttk.Button` and `tk.Button` in the same `panel_alt` bar.
- **Never** use `tk.Button` inside card content (use `ttk.Button` with a style).
- **Never** leave `height` unspecified on a `ttk.Treeview`.
- **Never** use `stretch=True` on Treeview columns (breaks horizontal scroll).
- **Never** create a fourth button type not listed in this catalogue.
