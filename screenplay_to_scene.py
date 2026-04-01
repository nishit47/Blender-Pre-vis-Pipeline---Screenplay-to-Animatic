#!/usr/bin/env python3
"""
Screenplay to Blender Scene — Fully Automated Pipeline
=======================================================

Reads a screenplay excerpt, uses Groq (llama-3.3-70b) to generate a complete
scene JSON, then runs flexible_scene_generator_optimized.py to build the Blender file.
No manual JSON writing needed.

Setup (one time):
    1. Get a free API key at https://console.groq.com  (no credit card needed)
    2. export GROQ_API_KEY=your_key_here   (or add to ~/.zshrc)

Usage:
    python3 screenplay_to_scene.py --file my_scene.txt
    python3 screenplay_to_scene.py --file my_scene.txt --output my_scene.blend
    python3 screenplay_to_scene.py --file my_scene.txt --json-only
    python3 screenplay_to_scene.py "INT. KITCHEN - DAY ..."
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

# --------------------------------------------------------------------------- #
# Asset + animation reference fed to the LLM with every request               #
# --------------------------------------------------------------------------- #

ASSET_REFERENCE = """
=== CHARACTER PACKS & ANIMATIONS ===

Pack: Animated Men Pack-glb        armature_filter: HumanArmature   scale: 1x
  Files: Man.glb, Man in Suit.glb, Man in Long Sleeves.glb
  Animations (EXACT names — use ONLY these):
    Man_Idle, Man_Walk, Man_Run, Man_Death, Man_Punch,
    Man_Jump, Man_Clapping, Man_Sitting, Man_SwordSlash
  USAGE GUIDE:
    Man_Idle      = standing still, talking, waiting, shocked, drawing a weapon (no draw anim)
    Man_Walk      = walking at normal pace
    Man_Run       = running / fleeing fast
    Man_Clapping  = waving hello, waving goodbye, clapping, any greeting gesture
    Man_Punch     = ONLY for actual physical punches/fighting
    Man_SwordSlash= ONLY for sword/melee weapon swing
    Man_Death     = ONLY for dying/collapsing
    Man_Sitting   = lower body pose for sitting; pair with lower_body_lock (see below)
    Man_Jump      = ONLY for jumping

  LOWER BODY LOCK:
    When a character is SEATED and does something with their upper body (talks, shoots,
    waves, picks something up), use:
      "action_filter": "<upper_body_anim>",   ← what the upper body plays
      "lower_body_lock": "Man_Sitting"         ← lower body is frozen in sitting pose
    The system will merge both actions at runtime, locking the legs in place.
    Example: seated character shoots → action_filter: "Man_Punch", lower_body_lock: "Man_Sitting"
    Example: seated character idles  → action_filter: "Man_Idle",  lower_body_lock: "Man_Sitting"

Pack: Animated Women Pack-glb      armature_filter: HumanArmature   scale: 1x
  Files: Woman.glb, Woman Casual.glb, Woman in Dress.glb, Woman in Tank Top.glb
  Animations (EXACT names — use ONLY these):
    Female_Idle, Female_Walk, Female_Run, Female_Death, Female_Punch,
    Female_Jump, Female_Clapping, Female_Sitting, Female_SwordSlash
  USAGE GUIDE:
    Female_Idle      = standing still, talking, waiting, shocked, doing chores, etc.
    Female_Walk      = walking at normal pace
    Female_Run       = running / fleeing fast
    Female_Clapping  = waving hello, waving goodbye, clapping, any greeting gesture
    Female_Punch     = ONLY for actual physical punches/fighting
    Female_SwordSlash= ONLY for sword/melee weapon swing
    Female_Death     = ONLY for dying/collapsing
    Female_Sitting   = lower body pose for sitting; pair with lower_body_lock (see below)
    Female_Jump      = ONLY for jumping

  LOWER BODY LOCK:
    When a character is SEATED and does something with their upper body (talks, shoots,
    waves, picks something up), use:
      "action_filter": "<upper_body_anim>",     ← what the upper body plays
      "lower_body_lock": "Female_Sitting"        ← lower body is frozen in sitting pose
    Example: seated character holds gun → action_filter: "Female_Idle", lower_body_lock: "Female_Sitting"

Pack: Ultimate Modular Men Pack-glb    armature_filter: CharacterArmature   scale: 2.5x
  Files: Business Man.glb, Casual Character.glb, Worker.glb, Adventurer.glb,
         Hoodie Character.glb, Farmer.glb, Astronaut.glb, King.glb, Punk.glb, Swat.glb
  Animations (EXACT names — use ONLY these):
    Idle, Walk, Run, Death, Punch_Left, Punch_Right, Kick_Left, Kick_Right,
    Sword_Slash, Gun_Shoot, Idle_Gun, Run_Shoot, Roll, Wave, Interact

Pack: Ultimate Modular Women Pack-glb  armature_filter: CharacterArmature   scale: 2.5x
  Files: Adventurer.glb, Worker.glb, Soldier.glb, Suit.glb, Witch.glb,
         Medieval.glb, Punk.glb, Sci Fi Character.glb
  Animations (EXACT names — use ONLY these, same as Ultimate Modular Men):
    Idle, Walk, Run, Death, Punch_Left, Punch_Right, Kick_Left, Kick_Right,
    Sword_Slash, Gun_Shoot, Idle_Gun, Run_Shoot, Roll, Wave, Interact
  NOTE: The Ultimate Modular packs DO have "Wave". The Animated (basic) packs do NOT.

⚠️  CRITICAL: NEVER invent animation names. Only use the exact names listed above.

Pack: Animated Animal Pack-glb     armature_filter: AnimalArmature   scale: 1x
  Files: Wolf.glb, Fox.glb, Shiba Inu.glb, Horse.glb, Cow.glb, Dog.glb, Cat.glb
  Animations: Idle, Walk, Gallop, Death, Eating, Attack, Jump_ToIdle,
              Attack_Headbutt, Attack_Kick

=== LOCATION ENVIRONMENTS ===

Use ONE of these as asset_path for a complete pre-built environment.
The scale value is a multiplier applied on top of import scale.

  assets/locations/Cozy Kitchen.glb     scale: 7.5
  assets/locations/Living Room.glb      scale: 5.0
  assets/locations/bedroom.glb          scale: 4.0
  assets/locations/Bar scene.glb        scale: 1.0
  assets/locations/Beach.glb            scale: 0.2
  assets/locations/Gas Station.glb      scale: 7.0
  assets/locations/Little Shop.glb      scale: 12.0
  assets/locations/Ocean.glb            scale: 1.0

If no matching pre-built scene exists, use individual props from:
  assets/Ultimate House Interior Pack-glb/   (scale 1.5x each)
  assets/Stylized Nature MegaKit.undefined-glb/  (scale 1x each)
  assets/kenney_city-kit-commercial_2.1/    (scale 20x each)
  assets/Medieval Village Pack-glb/         (scale 8x each)

=== PROP ASSETS & BONE ATTACHMENT ===

Attach props to a character's hand by specifying the character name and their grip bone.

Grip bones (right hand / left hand):
  Animated Men Pack   → Palm.R  / Palm.L
  Animated Women Pack → Palm.R  / Palm.L
  Ultimate Modular    → Wrist.R / Wrist.L

Props live in these packs. Prop scale = real-world size in METRES (the code auto-compensates
for the armature's internal scale, so just use realistic physical dimensions):
  assets/Ultimate Guns Pack-glb/
    Pistol.glb          → scale 0.20   (20 cm pistol)
    Revolver.glb        → scale 0.22
    Assault Rifle.glb   → scale 0.70
    Sniper Rifle.glb    → scale 0.90
    Shotgun.glb         → scale 0.75
    Submachine Gun.glb  → scale 0.45
    Bullpup.glb         → scale 0.55
    Bayonet.glb         → scale 0.30

  assets/Survival Pack-glb/
    Axe.glb             → scale 0.40
    Knife.glb           → scale 0.25
    Pan.glb             → scale 0.30
    Phone.glb           → scale 0.12
    Backpack.glb        → scale 0.40
    First Aid Kit.glb   → scale 0.20
    Gas Can.glb         → scale 0.25
    Compass.glb         → scale 0.08
    Matchbox.glb        → scale 0.06

Only add a "props" key when a character is explicitly holding something.
Omit "props" entirely for scenes with no hand-held items.

position_offset: [x, y, z] in bone-local space. ALWAYS use [0, 0, 0] — this places the
  prop exactly at the bone node. The armature scale is ~100, so any non-zero value
  creates a large world-space offset. Only change if explicitly told to.
rotation_offset: [x, y, z] radians to orient the prop in the grip.
  A gun pointing forward typically needs [1.5708, 0, 0] (90° around X).

=== COORDINATE SYSTEM & MOVEMENT RULES ===

- All characters face the NEGATIVE Y direction (-Y) by default.
- FORWARD movement: start_position Y > end_position Y  (Y decreases = forward).
- BACKWARD movement: Y increases — avoid unless intentional.
- Higher Y = further from camera. Lower Y = closer to camera.
- start_rotation / end_rotation are Z-axis OFFSETS in radians (do NOT touch X or Y):
    0.0      = default facing (-Y)
    3.14159  = 180° turn (now faces +Y)
    1.5708   = 90° turn left
   -1.5708   = 90° turn right
- Characters standing still: start_position == end_position.
- Keep all characters at Z=0 (floor level).
- Separate characters by at least 1 unit on X to avoid overlap.

=== TIMING RULES ===

- Use seconds for all timing (start_second, end_second), not frames.
- Walking across a room: 3–6 seconds.
- Quick reaction / turn: 0.5–1 second.
- Wave / greeting gesture: 2–3 seconds.
- Idle hold during dialogue: as long as needed.
- duration_seconds = latest end_second across all characters.
- frame_rate is always 24.

=== SYNCHRONIZATION RULES ===

- Characters must react AFTER the event that triggers them — never before.
  Example: if RAUL arrives at second 3, ADRIANA must not turn until second 3 or later.
- To find when a character arrives: look at the end_second of their walk segment.
- Build a shared event timeline first, then assign each character's segments around it.

=== INTERPERSONAL DISTANCE RULES ===

- Two characters having a conversation: stop 1.5–2.0 units apart on Y (not closer).
- Passing by / brief interaction: 1.0–1.5 units apart.
- NEVER place two characters within 1.0 units of each other on Y — they will clip.
  Example: if ADRIANA is at Y=4, RAUL should stop at Y=6 (2 units away), not Y=5.
- Separate characters on X by at least 1 unit to avoid side-by-side overlap.

=== ROTATION & FACING RULES ===

All characters start facing -Y (rot_z = 0.0) by default.
When two characters face each other across Y, one faces -Y (0.0) and the other faces +Y (3.14159).

A character MUST turn when any of these happen in the screenplay:
  - "turns around", "turns to face", "looks at", "hears X and turns"
  - A character who was walking toward someone and stops — they are already facing them.
    A character who was doing something else (e.g. dishes) and becomes aware — they must turn.
  - After a turn, they STAY at the new rotation for all remaining segments.

A turn MUST be three consecutive segments:
    1. Idle/wait segment   — holds at old rotation, interpolation CONSTANT
    2. Turn segment        — 0.5–1s, BEZIER, start_rotation = old, end_rotation = new
    3. Post-turn segment   — holds at new rotation, interpolation CONSTANT

⚠️  NEVER skip the turn segment. If a character's rotation changes at any point, there
    MUST be an explicit BEZIER segment showing the rotation changing.
⚠️  NEVER change rotation silently between segments — if seg[i].end_rotation != seg[i+1].start_rotation
    without a BEZIER segment between them, that is a bug.

=== ANIMATION SEQUENCE CONTINUITY RULES ===

- animation_sequence segments must be CONTIGUOUS — no gaps, no overlaps.
  The start_second of each segment must exactly equal the end_second of the previous one.
- Every character must have a segment covering every second from 0 to duration_seconds.
  Use Female_Idle / Man_Idle to fill any waiting periods.
- Rotation must be CONSISTENT across the whole sequence:
  whatever rot_z a segment ends with, the next segment must START with the same value.
"""

JSON_FORMAT = """
=== REQUIRED JSON FORMAT ===

{
  "scene": {
    "name": "INT. LOCATION - TIME",
    "duration_seconds": 12,
    "frame_rate": 24
  },
  "characters": [
    {
      "name": "CHARACTER_NAME",
      "asset_path": "assets/Pack-glb/Character.glb",
      "armature_filter": "HumanArmature",
      "scale": [1, 1, 1],
      "animation_sequence": [
        {
          "action_filter": "Man_Walk",
          "start_second": 0,
          "end_second": 6,
          "start_position": [0, 8, 0],
          "end_position": [0, 3, 0],
          "start_rotation": [0, 0, 0],
          "end_rotation": [0, 0, 0],
          "interpolation": "LINEAR"
        },
        {
          "action_filter": "Man_Idle",
          "lower_body_lock": "Man_Sitting",
          "start_second": 6,
          "end_second": 12,
          "start_position": [0, 3, 0],
          "end_position": [0, 3, 0],
          "start_rotation": [0, 0, 0],
          "end_rotation": [0, 0, 0],
          "interpolation": "CONSTANT"
        }
      ]
    }
  ],
  "environment": {
    "scattered_elements": [
      {
        "name": "LocationScene",
        "asset_path": "assets/locations/Cozy Kitchen.glb",
        "scale": [7.5, 7.5, 7.5],
        "count": 1,
        "random_seed": 1,
        "scatter_area": {
          "x_range": [0, 0],
          "y_range": [0, 0],
          "z_position": 0
        }
      }
    ]
  },
  "props": [
    {
      "name": "Pistol",
      "asset_path": "assets/Ultimate Guns Pack-glb/Pistol.glb",
      "scale": [0.20, 0.20, 0.20],
      "attach_to": "CHARACTER_NAME",
      "bone": "Palm.R",
      "position_offset": [0, 0, 0],
      "rotation_offset": [1.5708, 0, 0]
    }
  ],
  "camera": {
    "position": [7, 2, 3],
    "rotation": [1.2, 0, 1.4],
    "lens_mm": 35
  },
  "lighting": [
    {"type": "SUN",   "position": [5, 5, 10], "energy": 3.0},
    {"type": "POINT", "position": [0, 3, 4],  "energy": 800.0}
  ]
}

RULES:
- Do NOT include a "base_surface" key. Pre-built location GLBs already have their own floors.
- Only include "props" if characters are explicitly holding something. Omit the key entirely otherwise.
- scale in scattered_elements: all three values equal the location's scale above.
- interpolation: "LINEAR" when moving, "CONSTANT" when stationary, "BEZIER" when turning.
- lower_body_lock: ONLY include when the character is seated AND doing something with their upper body.
  Omit the key entirely for standing characters. Value must be "Man_Sitting" or "Female_Sitting".
- Turning segment: 0.5–1s long, end_rotation Z differs by 3.14159, interpolation "BEZIER".
- Camera rotation is in radians. Interior side-angle: roughly [1.2, 0, 1.4].
- Pre-built location: count 1, scatter_area x_range [0,0], y_range [0,0].
- Output ONLY raw valid JSON — no explanation, no markdown fences, nothing else.
"""


class ScreenplayToScene:
    """screenplay text  →  LLM JSON generation  →  .blend file"""

    def __init__(self, backend: str = "auto", model: str = None):
        self.script_dir = Path(__file__).parent.absolute()
        sys.path.insert(0, str(self.script_dir))
        from llm_client import LLMClient
        self.llm = LLMClient(backend=backend, model=model)

    # ------------------------------------------------------------------ #
    # JSON extraction + validation                                         #
    # ------------------------------------------------------------------ #

    def _extract_json(self, text: str) -> dict | None:
        candidates = []

        # fenced code block first
        for m in re.finditer(r'```(?:json)?\s*([\s\S]*?)\s*```', text):
            candidates.append(m.group(1).strip())

        # bare JSON object
        for m in re.finditer(r'(\{[\s\S]*\})', text):
            candidates.append(m.group(1).strip())

        for c in candidates:
            try:
                data = json.loads(c)
                if self._validate(data):
                    return data
            except json.JSONDecodeError:
                continue
        return None

    def _validate(self, data: dict) -> bool:
        for key in ['scene', 'characters', 'environment', 'camera', 'lighting']:
            if key not in data:
                print(f"  ⚠️  Missing key: '{key}'")
                return False
        if not data['characters']:
            print("  ⚠️  No characters in JSON")
            return False
        for c in data['characters']:
            if 'animation_sequence' not in c:
                print(f"  ⚠️  '{c.get('name','?')}' missing animation_sequence")
                return False
        return True

    # ------------------------------------------------------------------ #
    # Prompt                                                               #
    # ------------------------------------------------------------------ #

    def _build_prompt(self, screenplay: str) -> str:
        return f"""You are a pre-visualization system that converts screenplay scenes into 3D Blender scene configurations.

{ASSET_REFERENCE}

{JSON_FORMAT}

=== SCREENPLAY ===

{screenplay}

=== TASK ===

Analyze the screenplay and generate the complete JSON configuration.

Before writing JSON, reason through these steps in order:

STEP 1 — LOCATION & CHARACTERS
  - Pick the best pre-built environment.
  - Pick the best GLB and animation pack for each character.

STEP 2 — SHARED EVENT TIMELINE
  - List every event and the EXACT second it happens.
    e.g. "RAUL walk ends → second 4.0", "ADRIANA hears footsteps → second 4.0"
  - No character may react before the event that triggers them.
  - Derive all segment boundaries from this timeline.

STEP 3 — POSITIONS & DISTANCES
  - Place characters with realistic Y separation (1.5–2.0 units for conversation).
  - Separate on X by at least 1 unit.
  - Forward direction = decreasing Y (rot_z=0 faces -Y, rot_z=3.14159 faces +Y).
  - A character WALKING TOWARD another is already facing them — no extra turn needed.
  - A character DOING SOMETHING ELSE who then becomes aware of another person MUST turn.

STEP 4 — ROTATION AUDIT (do this before writing sequences)
  For EACH character, ask:
    a) What direction are they facing at the START? (default 0.0 = facing -Y)
    b) Does the screenplay say they turn, look at someone, or become aware of someone nearby?
    c) If yes: at what SECOND does the trigger happen?
    d) Insert a BEZIER turn segment (0.5–1s) at that second.
    e) After the turn, ALL subsequent segments hold the new rotation.
  Write out this audit in your reasoning before producing the JSON.

STEP 5 — ANIMATION SEQUENCES
  - Each character's segments must be CONTIGUOUS from second 0 to duration_seconds.
  - Fill all waiting time with Idle.
  - Rotation must be CONSISTENT: end_rotation of seg[i] = start_rotation of seg[i+1].
  - Only use EXACT animation names from the asset list above.

STEP 6 — PROPS
  - Only include props if a character is explicitly holding something in the scene.
  - Use the correct grip bone for the character's pack (Palm.R or Wrist.R).
  - Omit the "props" key entirely if no props are needed.

STEP 7 — CAMERA
  - Position to frame the main action from a cinematic angle.

Output ONLY the raw JSON object. No explanation. No markdown. Nothing else.
"""

    # ------------------------------------------------------------------ #
    # Post-processing: fix known LLM mistakes before writing to disk      #
    # ------------------------------------------------------------------ #

    # Per-location floor Z offset: characters are placed at Z=0 by the LLM but each
    # pre-built GLB has its own internal floor height after scaling.
    # These values lower characters so they stand on the actual visible floor surface.
    # Key = substring of asset_path (lowercase), value = Z offset to ADD to all character positions.
    _LOCATION_FLOOR_Z = {
        "cozy kitchen":  -3.7,
        # Other locations default to 0.0 — user adjusts manually in Blender if needed.
    }

    # Map of invented / incorrect animation names → correct names
    _ANIM_FIXUPS = {
        # Animated packs have no Wave — map to Clapping
        "Female_Wave":       "Female_Clapping",
        "Female_Greeting":   "Female_Clapping",
        "Female_Hello":      "Female_Clapping",
        "Female_Waving":     "Female_Clapping",
        "Man_Wave":          "Man_Clapping",
        "Man_Greeting":      "Man_Clapping",
        "Man_Hello":         "Man_Clapping",
        "Man_Waving":        "Man_Clapping",
        # Animated packs have no Hit/Shocked/Surprised/Draw
        "Female_Hit":        "Female_Idle",
        "Female_Shocked":    "Female_Idle",
        "Female_Surprised":  "Female_Idle",
        "Female_DrawGun":    "Female_Idle",
        "Female_AimGun":     "Female_Idle",
        "Female_Shoot":      "Female_Idle",
        "Man_Hit":           "Man_Idle",
        "Man_Shocked":       "Man_Idle",
        "Man_Surprised":     "Man_Idle",
        "Man_DrawGun":       "Man_Idle",
        "Man_AimGun":        "Man_Idle",
        "Man_Shoot":         "Man_Idle",
        # Animated packs have no Crouch/Duck/Dodge
        "Female_Crouch":     "Female_Idle",
        "Man_Crouch":        "Man_Idle",
        "Female_Dodge":      "Female_Run",
        "Man_Dodge":         "Man_Run",
        # Talking / gesturing — map to idle (no dedicated talk anim)
        "Female_Talk":       "Female_Idle",
        "Female_Talking":    "Female_Idle",
        "Female_Speak":      "Female_Idle",
        "Man_Talk":          "Man_Idle",
        "Man_Talking":       "Man_Idle",
        "Man_Speak":         "Man_Idle",
    }

    def _postprocess(self, data: dict) -> dict:
        """Auto-correct common LLM errors in generated JSON."""
        chars = data.get("characters", [])

        # 0a. Remove base_surface — pre-built GLBs already have floors
        env = data.get("environment", {})
        if "base_surface" in env:
            del env["base_surface"]
            print("  🔧 Removed redundant base_surface (pre-built GLB has its own floor)")

        # 0b. Apply per-location floor Z offset so characters land on the actual floor
        floor_z = 0.0
        for element in env.get("scattered_elements", []):
            asset_lower = element.get("asset_path", "").lower()
            for key, offset in self._LOCATION_FLOOR_Z.items():
                if key in asset_lower:
                    floor_z = offset
                    print(f"  🔧 Applying floor Z offset {offset:+.1f}m for '{key}'")
                    break
            if floor_z != 0.0:
                break

        if floor_z != 0.0:
            for char in chars:
                for seg in char.get("animation_sequence", []):
                    for key in ["start_position", "end_position"]:
                        seg[key][2] += floor_z

        # 1. Fix invented animation names
        for char in chars:
            for seg in char.get("animation_sequence", []):
                af = seg.get("action_filter", "")
                if af in self._ANIM_FIXUPS:
                    fixed = self._ANIM_FIXUPS[af]
                    print(f"  🔧 Auto-fixed animation: '{af}' → '{fixed}' ({char['name']})")
                    seg["action_filter"] = fixed

        # 2. Fix character X overlap (same X position)
        x_positions = {}
        for char in chars:
            seq = char.get("animation_sequence", [])
            if not seq:
                continue
            x = seq[0].get("start_position", [0, 0, 0])[0]
            while x in x_positions:
                x += 1.0
            if x != seq[0]["start_position"][0]:
                print(f"  🔧 Auto-fixed X overlap for '{char['name']}': offset to X={x}")
                for seg in seq:
                    seg["start_position"][0] = x
                    seg["end_position"][0]   = x
            x_positions[x] = char["name"]

        # 3. Inject missing BEZIER turn segments
        for char in chars:
            seq = char.get("animation_sequence", [])
            i = 0
            while i < len(seq) - 1:
                cur  = seq[i]
                nxt  = seq[i + 1]
                cur_rot = cur.get("end_rotation",   [0, 0, 0])
                nxt_rot = nxt.get("start_rotation", [0, 0, 0])
                # If Z rotation changes and the next segment is not already a BEZIER turn
                if abs(cur_rot[2] - nxt_rot[2]) > 0.01 and nxt.get("interpolation") != "BEZIER":
                    turn_dur  = 0.75  # seconds for the turn
                    turn_end  = nxt["start_second"]
                    turn_start = max(cur["start_second"], turn_end - turn_dur)
                    pos = nxt.get("start_position", [0, 0, 0])[:]
                    turn_seg = {
                        "action_filter":  cur.get("action_filter", "Female_Idle"),
                        "start_second":   turn_start,
                        "end_second":     turn_end,
                        "start_position": pos,
                        "end_position":   pos,
                        "start_rotation": [0, 0, cur_rot[2]],
                        "end_rotation":   [0, 0, nxt_rot[2]],
                        "interpolation":  "BEZIER",
                    }
                    # Shorten the preceding segment to end where the turn starts
                    cur["end_second"] = turn_start
                    cur["end_rotation"] = [0, 0, cur_rot[2]]
                    seq.insert(i + 1, turn_seg)
                    print(f"  🔧 Injected turn segment for '{char['name']}' "
                          f"({cur_rot[2]:.2f}→{nxt_rot[2]:.2f} rad) "
                          f"at {turn_start:.1f}–{turn_end:.1f}s")
                    i += 2  # skip over the newly inserted segment
                else:
                    i += 1

        # 4. Enforce default prop scales (bone-local space, not multiplicative)
        # Prop scale = desired world size in metres. These are defaults per prop name;
        # the generator divides by the armature's world scale automatically.
        _prop_default_scales = {
            "pistol":         0.20,
            "revolver":       0.22,
            "assault rifle":  0.70,
            "sniper rifle":   0.90,
            "shotgun":        0.75,
            "submachine gun": 0.45,
            "bullpup":        0.55,
            "bayonet":        0.30,
            "axe":            0.40,
            "knife":          0.25,
            "pan":            0.30,
            "phone":          0.12,
            "backpack":       0.40,
            "first aid kit":  0.20,
            "gas can":        0.25,
            "compass":        0.08,
            "matchbox":       0.06,
        }
        for prop in data.get("props", []):
            prop_name_lower = prop.get("name", "").lower()
            current = prop.get("scale", [1, 1, 1])
            # Apply default scale if still at generic [1,1,1]
            if current == [1, 1, 1]:
                for key, default_m in _prop_default_scales.items():
                    if key in prop_name_lower:
                        prop["scale"] = [default_m, default_m, default_m]
                        print(f"  🔧 Set prop scale to {default_m}m for '{prop.get('name')}'")
                        break
            # Always force position_offset to [0,0,0] — any non-zero value in
            # bone-local space gets amplified by the armature's ~100x scale
            if prop.get("position_offset", [0, 0, 0]) != [0, 0, 0]:
                print(f"  🔧 Forced position_offset to [0,0,0] for '{prop.get('name')}'")
                prop["position_offset"] = [0, 0, 0]

        # 5. Validate prop bone names match the pack used by that character

        _pack_bones = {
            "animated men pack":    {"right": "Palm.R",  "left": "Palm.L"},
            "animated women pack":  {"right": "Palm.R",  "left": "Palm.L"},
            "ultimate modular men": {"right": "Wrist.R", "left": "Wrist.L"},
            "ultimate modular women":{"right": "Wrist.R","left": "Wrist.L"},
        }
        char_pack_map = {}
        for char in chars:
            ap = char.get("asset_path", "").lower()
            for pack_key in _pack_bones:
                if pack_key in ap:
                    char_pack_map[char["name"]] = pack_key
                    break

        for prop in data.get("props", []):
            attach_to = prop.get("attach_to", "")
            bone      = prop.get("bone", "")
            pack      = char_pack_map.get(attach_to)
            if pack:
                valid_bones = list(_pack_bones[pack].values())
                if bone not in valid_bones:
                    # Pick the correct right-hand bone from the actual pack
                    correct = _pack_bones[pack]["right"]
                    print(f"  🔧 Fixed prop bone '{bone}' → '{correct}' "
                          f"(correct for {pack})")
                    prop["bone"] = correct

        # 7. Enforce minimum Y distance between stationary characters (1.5 units)
        # Find final resting Y positions for all chars
        resting = {}
        for char in chars:
            seq = char.get("animation_sequence", [])
            if seq:
                last = seq[-1]
                resting[char["name"]] = last.get("end_position", [0, 0, 0])[1]

        names = list(resting.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                yi = resting[names[i]]
                yj = resting[names[j]]
                if abs(yi - yj) < 1.5:
                    # Push apart to 1.5 units
                    mid = (yi + yj) / 2
                    new_yi = mid - 0.75
                    new_yj = mid + 0.75
                    diff_i = new_yi - yi
                    diff_j = new_yj - yj
                    print(f"  🔧 Enforced Y distance: '{names[i]}' shifted by {diff_i:+.2f}, "
                          f"'{names[j]}' shifted by {diff_j:+.2f}")
                    for char in chars:
                        if char["name"] == names[i]:
                            for seg in char.get("animation_sequence", []):
                                for key in ["start_position", "end_position"]:
                                    seg[key][1] += diff_i
                        if char["name"] == names[j]:
                            for seg in char.get("animation_sequence", []):
                                for key in ["start_position", "end_position"]:
                                    seg[key][1] += diff_j

        # 8. Facing check: if two characters end up interacting close together,
        #    make sure they are facing each other rather than both facing the same way.
        #    "Interacting" = final action is NOT Walk/Run (they've stopped and are doing something).
        import math
        _WALK_ANIMS = {"Female_Walk", "Man_Walk", "Female_Run", "Man_Run"}
        interactive_chars = []
        for char in chars:
            seq = char.get("animation_sequence", [])
            if not seq:
                continue
            last_anim = seq[-1].get("action_filter", "")
            if last_anim not in _WALK_ANIMS:
                last_y = seq[-1].get("end_position", [0, 0, 0])[1]
                last_rot = seq[-1].get("end_rotation", [0, 0, 0])[2]
                interactive_chars.append({"char": char, "y": last_y, "rot_z": last_rot})

        if len(interactive_chars) == 2:
            a, b = interactive_chars[0], interactive_chars[1]
            # Expected: character at lower Y should face +Y (π), higher Y should face -Y (0)
            lower, higher = (a, b) if a["y"] < b["y"] else (b, a)
            expected_lower  = math.pi   # face +Y (toward higher)
            expected_higher = 0.0       # face -Y (toward lower)

            def _needs_face_fix(actual_rot, expected_rot, tol=0.3):
                return abs(actual_rot - expected_rot) > tol

            def _inject_face_turn(char_entry, from_rot, to_rot):
                seq = char_entry["char"].get("animation_sequence", [])
                if not seq:
                    return
                # Find the first non-Walk segment from the end
                for i in range(len(seq) - 1, -1, -1):
                    seg = seq[i]
                    if seg.get("action_filter", "") not in _WALK_ANIMS:
                        # Insert a BEZIER turn at the start of this segment
                        turn_dur   = 0.75
                        turn_start = seg["start_second"]
                        turn_end   = min(turn_start + turn_dur, seg["end_second"])
                        pos = seg.get("start_position", [0, 0, 0])[:]

                        turn_seg = {
                            "action_filter":  seg.get("action_filter", "Female_Idle"),
                            "start_second":   turn_start,
                            "end_second":     turn_end,
                            "start_position": pos,
                            "end_position":   pos,
                            "start_rotation": [0, 0, from_rot],
                            "end_rotation":   [0, 0, to_rot],
                            "interpolation":  "BEZIER",
                        }
                        # Shift the existing segment to start after the turn
                        seg["start_second"] = turn_end
                        seg["start_rotation"] = [0, 0, to_rot]
                        # Also patch end_rotation on the segment right after the turn
                        # so it stays consistent (start=to_rot, end=to_rot)
                        if seg.get("end_rotation", [0, 0, 0])[2] != to_rot:
                            seg["end_rotation"] = [0, 0, to_rot]
                        seq.insert(i, turn_seg)
                        # Propagate the new rotation to ALL later segments
                        for k in range(i + 2, len(seq)):
                            seq[k]["start_rotation"] = [0, 0, to_rot]
                            seq[k]["end_rotation"]   = [0, 0, to_rot]
                        print(f"  🔧 Facing fix: '{char_entry['char']['name']}' "
                              f"turn {from_rot:.2f}→{to_rot:.2f} rad at {turn_start:.1f}s "
                              f"(faces other character)")
                        return

            if _needs_face_fix(lower["rot_z"], expected_lower):
                _inject_face_turn(lower, lower["rot_z"], expected_lower)
            if _needs_face_fix(higher["rot_z"], expected_higher):
                _inject_face_turn(higher, higher["rot_z"], expected_higher)

        return data

    # ------------------------------------------------------------------ #
    # Pipeline                                                             #
    # ------------------------------------------------------------------ #

    def generate_json(self, screenplay: str) -> dict | None:
        print("\n📜 Sending screenplay to Groq...")

        for attempt in range(1, 4):
            try:
                print(f"  🤖 Attempt {attempt}/3")
                raw  = self.llm.call(self._build_prompt(screenplay))
                data = self._extract_json(raw)
                if data:
                    print("  ✅ Valid JSON received")
                    return data
                print("  ⚠️  Could not parse JSON from response — retrying...")
            except Exception as e:
                print(f"  ⚠️  {e}")

        print("\n❌ Failed after 3 attempts.")
        return None

    def run(
        self,
        screenplay:   str,
        output_blend: str  = None,
        json_path:    str  = None,
        json_only:    bool = False,
    ) -> bool:

        # Step 1 — generate JSON
        data = self.generate_json(screenplay)
        if not data:
            return False

        # Step 1b — auto-fix common LLM mistakes
        data = self._postprocess(data)

        # Step 2 — save JSON
        if json_path is None:
            safe = re.sub(r'[^a-zA-Z0-9_]', '_', data['scene']['name']).lower().strip('_')
            json_path = str(self.script_dir / f"{safe}.json")

        with open(json_path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"\n💾 JSON saved: {json_path}")

        if json_only:
            print("   (--json-only: stopping here)")
            return True

        # Step 3 — generate .blend
        print("\n🎬 Generating Blender scene...")
        sys.path.insert(0, str(self.script_dir))
        from flexible_scene_generator_optimized import OptimizedSceneGenerator

        if output_blend is None:
            output_blend = json_path.replace('.json', '.blend')

        ok = OptimizedSceneGenerator(json_path).generate_scene(output_blend, render_video=False)
        print(f"\n{'✅ Done:' if ok else '❌ Failed.'} {output_blend if ok else json_path}")
        return ok


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Screenplay → Blender scene, fully automated via LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 screenplay_to_scene.py --file my_scene.txt
  python3 screenplay_to_scene.py --file my_scene.txt --output scene.blend
  python3 screenplay_to_scene.py --file my_scene.txt --json-only
  python3 screenplay_to_scene.py "INT. KITCHEN - DAY\\nRICK walks in."
        """,
    )
    parser.add_argument('screenplay',  nargs='?',            help='Screenplay as inline text')
    parser.add_argument('--file',  '-f',                     help='Path to screenplay .txt file')
    parser.add_argument('--output', '-o',                    help='Output .blend path')
    parser.add_argument('--json-out', '-j',                  help='Output JSON path')
    parser.add_argument('--json-only', action='store_true',  help='Stop after saving JSON')
    parser.add_argument('--backend', default='auto',          help='LLM backend: groq | anthropic | gemini | openai (default: auto)')
    parser.add_argument('--model',   default=None,            help='Override model name for chosen backend')
    args = parser.parse_args()

    if args.file:
        try:
            screenplay = Path(args.file).read_text()
        except FileNotFoundError:
            print(f"❌ File not found: {args.file}")
            sys.exit(1)
    elif args.screenplay:
        screenplay = args.screenplay
    else:
        print("📝 Paste screenplay (Ctrl+D when done):")
        try:
            screenplay = sys.stdin.read()
        except KeyboardInterrupt:
            print("\n👋 Cancelled.")
            sys.exit(0)

    if not screenplay.strip():
        print("❌ No screenplay text provided.")
        sys.exit(1)

    print("=" * 60)
    print("🎬  SCREENPLAY → BLENDER  (Groq)")
    print("=" * 60)
    print(f"Input  : {len(screenplay)} characters")
    print()

    ok = ScreenplayToScene(backend=args.backend, model=args.model).run(
        screenplay,
        output_blend=args.output,
        json_path=args.json_out,
        json_only=args.json_only,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
