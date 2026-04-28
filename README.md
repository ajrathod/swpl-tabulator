# PacWest Inline Racing — Race Manager

A Python desktop app for managing indoor inline speed skating competitions.

---

## Requirements

```
pip install openpyxl pandas pillow
```

Python 3.8+ with tkinter (built-in on most systems).

---

## Running

```bash
python app.py
```

---

## Features

### Individual Events
1. **Load Individual Master Sheet** — imports the `.xlsx` master (Saturday Master, Sunday Master, etc.) and auto-detects:
   - Skater name, gender, age, panel number, club, and race group
   - Groups: Star Skate Tiny Tot → Beginner/Elite Freshman → Elite Junior/Senior, etc.

2. **Auto Heat Seeding** based on group size:
   - ≤ 6 skaters → straight final
   - 7–12 skaters → 2 heats (≤6 each), top 3 each → final of 6
   - 13+ skaters → heats → 2 semis → final of ≤6 (max 7)

3. **Event Types per group:**
   - `Heats / Semis / Final` — for standard distance races, Skills & Obstacle
   - `Direct Final` — for Points Race, Elimination Race, Open events (no heats needed)

4. **Operator View** — enter placements per heat/semi, click Save, then advance qualifiers
5. **Public View** — shows placement medals (🥇🥈🥉), read-only

### Relay Events
- Load a separate relay master sheet
- Teams displayed by color (Red / Green / Blue / Black / etc.) matching the original spreadsheet
- Same heat seeding logic applies

### Add Custom Events
In Operator View, click **+ Add Event** on any group to add ad-hoc events (e.g. a specific sprint distance).

---

## Color Scheme
Mirrors PacWest master sheet colors:
- `#FFCC99` header orange
- `#C0C0C0` gray columns
- `#EA9999` Saturday-only marker
- `#C9DAF8` Sunday-only marker
- `#70AD47` top placement green

---

## File Format Expected
The app reads the same `.xlsx` format as `MOHO-APR-25_26-Master.xlsx`:
- Column A: First Name
- Column B: Last Name
- Column C: M/F
- Column D: Age
- Column E: Panel Number
- Column F: Club
- Column I: Group (detected from group header rows)
- Group header rows have `X` in column E or F

---

## Extending
- To add Outdoor support: extend `_load_individual()` to ask Indoor/Outdoor and filter sheets accordingly
- To add DOB column: it's in column D of the source (same as Age in some sheets)
- To persist results: add JSON save/load around `self.events` using `dataclasses.asdict()`
