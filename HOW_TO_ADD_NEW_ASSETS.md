# How to Add New Assets

The system reads all available asset information directly from `README.md` at runtime. When you add new GLB files, update the README and the LLM will automatically know about them — no code changes needed.

## Steps

### 1. Add the GLB files to `assets/`

```
assets/
└── My New Pack-glb/
    ├── Item1.glb
    ├── Item2.glb
    └── Item3.glb
```

### 2. Add the pack to the folder structure in `README.md`

Find the **"Complete Asset Folder Structure"** section and add your pack alphabetically:

```markdown
├── My New Pack-glb
│   ├── Item1.glb
│   ├── Item2.glb
│   └── Item3.glb
```

### 3. Add a scaling guideline in `README.md`

Find the **"Asset Scaling Guidelines"** table and add a row:

```markdown
| My New Pack-glb | 1.5x |
```

### 4. (Optional) Add scene-specific usage notes

If your pack is best suited for a particular location type, add a note to the relevant location section (e.g., "Kitchen", "Office") in the **"Common Scene Locations & Required Assets"** section of the README.

### 5. Run the pipeline — that's it

```bash
python3 pipeline.py "INT. MY SCENE - DAY\n\nCharacter does something with Item1."
```

The LLM will now see `My New Pack-glb/Item1.glb` in its available asset list and can select it for appropriate scenes.

---

## What Updates Automatically

| Component | Source | Auto-updates? |
|-----------|--------|--------------|
| Asset pack names | README.md folder structure | ✅ Yes |
| GLB file names | README.md folder structure | ✅ Yes |
| Full asset paths | Combined from above | ✅ Yes |
| Scaling factors | README.md scaling table | ✅ Yes |
| Shown to LLM | Parsed from README at runtime | ✅ Yes |
| Validated by post-processor | Checked against README | ✅ Yes |
| Location design guidelines | Hardcoded in prompt | ❌ By design |

---

## Adding a Complete Location Scene (pre-built GLB)

If you have a complete pre-built scene GLB (like `Bar scene.glb`), place it in `assets/locations/` and add it to the locations table in the README:

```markdown
| My New Location.glb | 5x |
```

The LLM will use a `location_glb` block in the JSON instead of assembling from parts:

```json
"environment": {
  "location_glb": {
    "asset_path": "assets/locations/My New Location.glb",
    "scale": [5, 5, 5],
    "position": [0, 0, 0]
  }
}
```

Note the correct Z floor offset for your scene — you may need to add it to the `_LOCATION_FLOOR_Z` dictionary in `screenplay_to_scene.py`.

---

## Adding a New Character with Custom Animations

If you have a character GLB with animations already embedded (standard NLA strips):

1. Place the GLB in the appropriate pack folder (or create a new one).
2. List the exact animation action names in the README character section.
3. Add the armature filter name (the name of the Armature object inside the GLB).
4. Add to the scaling table.

The LLM will use the exact names you list. **Animation names must match exactly** (case-sensitive) — they are matched against `bpy.data.actions` by stripping the `ArmatureName|` prefix and `.001` suffixes.

### Building a combined GLB from per-animation FBX files

If you have separate FBX files for each animation (common with asset store packs):

```bash
# Edit build_character_glb.py to point to your FBX folder and gender prefix
python3 build_character_glb.py
```

This merges all FBX animations onto the base character mesh and exports a single `.glb` with all animations as NLA strips — ready for the pipeline.

---

## Common Mistakes

| Mistake | Correct approach |
|---------|-----------------|
| Add GLB but don't update README | Must update README — pipeline reads from it |
| Invent an animation name in JSON | Only use names explicitly listed in README |
| Use a multiplier for `scale_meters` | `scale_meters` is world-space metres, not a ratio |
| Add `base_surface` for a location GLB | Location GLBs have built-in floors — omit `base_surface` |
