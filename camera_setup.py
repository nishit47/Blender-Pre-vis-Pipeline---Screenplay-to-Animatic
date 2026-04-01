#!/usr/bin/env python3
"""
Phase 2 — Camera Setup
======================

Takes the Phase 1 .blend + .json + screenplay and adds a full multi-camera
setup to the scene.  Each shot gets its own named Camera object; Blender
timeline markers handle the cuts.

Two input modes:
  1. Director-specified  — explicit camera instructions in the screenplay
  2. AI-inferred         — LLM reads scene context and decides shots automatically

Supported LLM backends (default: groq, same as Phase 1):
  groq | anthropic | gemini | openai
  Set API key → auto-detected, or use --backend flag.

Usage:
    python3 camera_setup.py --blend scene.blend --json scene.json --file script.txt
    python3 camera_setup.py --blend scene.blend --json scene.json "JACK stares..."
    python3 camera_setup.py --blend scene.blend --json scene.json --cameras-only
    python3 camera_setup.py --blend scene.blend --json scene.json --cameras-json shots.json
    python3 camera_setup.py --blend scene.blend --json scene.json --backend anthropic
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Shot / movement reference fed to the LLM
# ---------------------------------------------------------------------------

CAMERA_REFERENCE = """
=== SHOT TYPES ===
ECU  Extreme Close Up   distance ~0.6m  — eye, hand, or object detail
CU   Close Up           distance ~1.5m  — face / raw emotion
MCU  Medium Close Up    distance ~2.5m  — chest-up, intimate dialogue
MS   Medium Shot        distance ~4.0m  — waist-up, action + reaction
WA   Wide Angle         distance ~8.0m  — full body, environment context
LOW  Low Angle          distance ~2.0m  height -1.5m — power, menace, threat
OVR  Overhead           height  +8.0m  — surveillance, vulnerability, god view

=== MOVEMENTS ===
STATIC      Camera is fixed. TRACK_TO constraint aims it at the subject.
            Best for: dialogue, reaction shots, establishing.

PUSH_IN     Camera dollies from start_distance → end_distance toward subject.
            Requires: start_distance, end_distance
            Best for: building tension, revelation, intimacy.

PUSH_OUT    Camera dollies away from subject.
            Requires: start_distance, end_distance
            Best for: isolation, aftermath, context reveal.

TRUCK       Camera slides laterally (side-to-side) at a fixed depth.
            Requires: truck_start_x, truck_end_x, truck_depth, truck_height
            Best for: following movement, revealing someone/something beside subject.

PAN         Camera stays fixed but rotates horizontally (yaw) to follow action.
            Requires: pan_start_angle, pan_end_angle  (degrees; 0=front of subject)
            Best for: following a character walking, scanning a room.

TILT        Camera stays fixed but rotates vertically — tilts up or down.
            Requires: tilt_start_z, tilt_end_z  (world Z where camera aims)
            Best for: revealing a gun on the table, scanning from face to floor.

CRANE       Camera rises or falls while aiming at subject.
            Requires: crane_start_z, crane_end_z  (camera height in world units)
            Best for: dramatic reveals, opening/closing a scene.

ORBIT       Camera arcs around the subject on a circle.
            Requires: orbit_radius, orbit_start_angle, orbit_end_angle, orbit_height
            Best for: 360 reveals, showing spatial relationship between characters.

=== ANGLE CONVENTION ===
(degrees around the subject, relative to the direction the character faces)
  0°  = directly in front (camera sees their face)
  90° = to their right side (profile)
 180° = directly behind them
 270° = to their left side

For two characters facing each other:
  character A at angle=0°   (camera behind B, seeing A)
  character B at angle=180° (camera behind A, seeing B)

=== COMPOSITION ===
CENTER  subject centred in frame
LEFT    subject on left third (looking into space on the right)
RIGHT   subject on right third (looking into space on the left)

=== TWO-SHOT ===
Set "subject" to an array: ["JACK", "LISA"]
Camera frames midpoint. Use MS or WA.

=== TIMING ===
- start_second / end_second define the cut window.
- A typical shot: 2–6 s.  Rapid cut: 1–2 s.  Slow/dramatic: 4–8 s.
- Every second of the scene must be covered.
"""

CAMERA_JSON_FORMAT = """
=== REQUIRED JSON FORMAT ===

{
  "camera_shots": [
    {
      "name": "Shot_01_MS_DAVID",
      "subject": "DAVID",
      "shot_type": "MS",
      "composition": "RIGHT",
      "angle": 0,
      "movement": "STATIC",
      "start_second": 0,
      "end_second": 3
    },
    {
      "name": "Shot_02_CU_AMANDA",
      "subject": "AMANDA",
      "shot_type": "CU",
      "composition": "LEFT",
      "angle": 180,
      "movement": "STATIC",
      "start_second": 3,
      "end_second": 6
    },
    {
      "name": "Shot_03_PUSH_IN_GUN",
      "subject": "AMANDA",
      "shot_type": "MCU",
      "composition": "CENTER",
      "angle": 45,
      "movement": "PUSH_IN",
      "start_second": 6,
      "end_second": 10,
      "start_distance": 4.0,
      "end_distance": 1.5
    },
    {
      "name": "Shot_04_TILT_DOWN",
      "subject": "AMANDA",
      "shot_type": "CU",
      "composition": "CENTER",
      "angle": 0,
      "movement": "TILT",
      "start_second": 10,
      "end_second": 13,
      "tilt_start_z": 4.1,
      "tilt_end_z": 0.5
    },
    {
      "name": "Shot_05_TRUCK_REVEAL",
      "subject": "DAVID",
      "shot_type": "MS",
      "composition": "CENTER",
      "angle": 90,
      "movement": "TRUCK",
      "start_second": 13,
      "end_second": 15,
      "truck_start_x": -5.0,
      "truck_end_x": 0.0,
      "truck_depth": 4.0,
      "truck_height": 2.8
    }
  ]
}

RULES:
- name must be unique.
- subject: string (single) or array of two strings (two-shot).
- shot_type: ECU | CU | MCU | MS | WA | LOW | OVR
- movement: STATIC | PUSH_IN | PUSH_OUT | TRUCK | PAN | TILT | CRANE | ORBIT
- PUSH_IN / PUSH_OUT  → start_distance, end_distance
- TRUCK               → truck_start_x, truck_end_x, truck_depth, truck_height
- PAN                 → pan_start_angle, pan_end_angle  (degrees)
- TILT                → tilt_start_z, tilt_end_z  (world Z target height)
- CRANE               → crane_start_z, crane_end_z  (camera world Z)
- ORBIT               → orbit_radius, orbit_start_angle, orbit_end_angle, orbit_height
- Cover every second from 0 to scene duration.
- Output ONLY raw valid JSON — no explanation, no markdown fences.
"""


# ---------------------------------------------------------------------------
# CameraSetup
# ---------------------------------------------------------------------------

class CameraSetup:
    TARGET_HEIGHT = 2.8

    SHOT_DISTANCES = {
        'ECU': 0.6, 'CU': 1.5, 'MCU': 2.5,
        'MS':  4.0, 'WA': 8.0, 'LOW': 2.0, 'OVR': 0.1,
    }
    SHOT_HEIGHT_OFFSETS = {
        'ECU': 0, 'CU': 0, 'MCU': 0,
        'MS':  0, 'WA': 0, 'LOW': -1.5, 'OVR': 8.0,
    }
    COMPOSITION_FACTOR = 0.25

    def __init__(self, backend: str = "auto", model: str = None):
        self.script_dir = Path(__file__).parent.absolute()
        sys.path.insert(0, str(self.script_dir))
        from llm_client import LLMClient
        self.llm = LLMClient(backend=backend, model=model)

    @property
    def model(self):
        return self.llm.model

    # ------------------------------------------------------------------ #
    # JSON extraction                                                       #
    # ------------------------------------------------------------------ #

    def _extract_json(self, text: str) -> dict | None:
        for m in re.finditer(r'```(?:json)?\s*([\s\S]*?)\s*```', text):
            try:
                d = json.loads(m.group(1).strip())
                if "camera_shots" in d:
                    return d
            except json.JSONDecodeError:
                pass
        for m in re.finditer(r'(\{[\s\S]*\})', text):
            try:
                d = json.loads(m.group(1).strip())
                if "camera_shots" in d:
                    return d
            except json.JSONDecodeError:
                pass
        return None

    # ------------------------------------------------------------------ #
    # Animation beat extraction                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_animation_events(scene_json: dict) -> list[dict]:
        events = []
        for char in scene_json.get("characters", []):
            name = char["name"]
            seq  = char.get("animation_sequence", [])
            prev_action = None
            for seg in seq:
                action    = seg["action_filter"]
                start_s   = seg["start_second"]
                start_p   = seg.get("start_position", [0, 0, 0])
                end_p     = seg.get("end_position",   [0, 0, 0])
                is_moving = start_p != end_p
                if action != prev_action or is_moving:
                    events.append({
                        "second":    start_s,
                        "character": name,
                        "action":    action,
                        "moving":    is_moving,
                        "position":  start_p,
                    })
                prev_action = action
        events.sort(key=lambda e: e["second"])
        return events

    # ------------------------------------------------------------------ #
    # LLM prompt                                                           #
    # ------------------------------------------------------------------ #

    def _build_prompt(self, screenplay: str | None, scene_json: dict) -> str:
        chars    = scene_json.get("characters", [])
        duration = scene_json.get("scene", {}).get("duration_seconds", 10)
        fps      = scene_json.get("scene", {}).get("frame_rate", 24)

        events = self._extract_animation_events(scene_json)
        beat_lines = [
            f"  t={e['second']:>5.1f}s  {e['character']:<12} → {e['action']:<25} "
            f"[{'MOVING' if e['moving'] else 'action change'}]  pos={e['position']}"
            for e in events
        ]

        char_summary = [
            f"  {c['name']}: starts at {c.get('animation_sequence', [{}])[0].get('start_position', '?')}"
            for c in chars
        ]

        return f"""You are a cinematographer designing cameras for a pre-visualization scene in Blender.

{CAMERA_REFERENCE}

{CAMERA_JSON_FORMAT}

=== SCENE METADATA ===
Duration  : {duration} seconds  ({duration * fps} frames, {fps} fps)
Characters:
{chr(10).join(char_summary)}

=== ANIMATION BEAT TIMELINE ===
Use these EXACT seconds as cut points — they are when something notable happens.

{chr(10).join(beat_lines) if beat_lines else "  (no events)"}

{f"=== SCREENPLAY ==={chr(10)}{screenplay}{chr(10)}" if screenplay and screenplay.strip() else ""}
=== YOUR TASK ===

Design a complete multi-camera shot list. Think like a film director:

1. START with an establishing/wide shot (WA or MS) to set the scene.
2. For each beat in the timeline, choose the MOST dramatic framing:
   - Action / movement → MS or WA
   - Dialogue / reaction → CU or MCU
   - Object reveal (gun, prop) → ECU or TILT down to it
3. Use PUSH_IN to build tension, TILT to reveal something on a counter/table,
   TRUCK to follow a character entering frame, CRANE for dramatic opening/close.
4. For two characters talking: alternate angle=0° on one and angle=180° on the other.
5. Add movement sparingly — 1 or 2 moving shots max; rest can be STATIC.
6. Cover EVERY second from 0 to {duration}.
7. Aim for {max(5, min(10, len(events) + 3))} shots total.

Output ONLY the raw JSON object. No explanation.
"""

    # ------------------------------------------------------------------ #
    # LLM call                                                             #
    # ------------------------------------------------------------------ #

    def generate_shots_json(self, screenplay: str | None, scene_json: dict) -> dict | None:
        print("\n🎬 Planning camera shots with AI...")
        for attempt in range(1, 4):
            try:
                print(f"  🤖 Attempt {attempt}/3")
                raw  = self.llm.call(self._build_prompt(screenplay, scene_json), max_tokens=4096)
                data = self._extract_json(raw)
                if data:
                    print(f"  ✅ {len(data['camera_shots'])} shots planned")
                    return data
                print("  ⚠️  Could not parse JSON — retrying...")
            except Exception as e:
                print(f"  ⚠️  {e}")
        print("❌ Failed after 3 attempts.")
        return None

    # ------------------------------------------------------------------ #
    # Blender script generation                                            #
    # ------------------------------------------------------------------ #

    def _generate_blender_camera_script(
        self, shots_data: dict, scene_json: dict, output_blend: str
    ) -> str:
        fps      = scene_json.get("scene", {}).get("frame_rate", 24)
        duration = scene_json.get("scene", {}).get("duration_seconds", 10)
        shots    = shots_data["camera_shots"]

        lines = [
            "import bpy, math, mathutils",
            "from math import radians, sin, cos, pi",
            "",
            f"fps      = {fps}",
            f"scene    = bpy.context.scene",
            f"scene.frame_start = 1",
            f"scene.frame_end   = {duration} * fps",
            "",
            f"TARGET_HEIGHT      = {self.TARGET_HEIGHT}",
            f"COMPOSITION_FACTOR = {self.COMPOSITION_FACTOR}",
            f"SHOT_DISTANCES     = {self.SHOT_DISTANCES}",
            f"SHOT_H_OFFSETS     = {self.SHOT_HEIGHT_OFFSETS}",
            "",
            "# ── Helpers ─────────────────────────────────────────────",
            "def get_subject_loc(subject):",
            "    # Use evaluated depsgraph so we read the ACTUAL position after NLA",
            "    # and any manual keyframe edits the user made after Phase 1.",
            "    dg = bpy.context.evaluated_depsgraph_get()",
            "    def _eval_loc(name):",
            "        obj = bpy.data.objects.get(name)",
            "        if obj is None: return None",
            "        return obj.evaluated_get(dg).matrix_world.translation.copy()",
            "    if isinstance(subject, list):",
            "        locs = [_eval_loc(n) for n in subject if _eval_loc(n) is not None]",
            "        if not locs: return mathutils.Vector((0,0,0))",
            "        mid = locs[0].copy()",
            "        for l in locs[1:]: mid += l",
            "        return mid / len(locs)",
            "    loc = _eval_loc(subject)",
            "    return loc if loc is not None else mathutils.Vector((0,0,0))",
            "",
            "def get_look_at(subject, name):",
            "    look = bpy.data.objects.get(name)",
            "    if look is None:",
            "        look = bpy.data.objects.new(name, None)",
            "        bpy.context.collection.objects.link(look)",
            "        look.empty_display_type = 'SPHERE'",
            "        look.empty_display_size  = 0.1",
            "        look.hide_select = look.hide_viewport = look.hide_render = True",
            "    # Always use world space — GLB armatures have a baked -90° X rotation",
            "    # so local (0,0,h) would point behind the character, not above them.",
            "    look.parent = None",
            "    loc = get_subject_loc(subject)",
            "    look.location = (loc.x, loc.y, loc.z + TARGET_HEIGHT)",
            "    return look",
            "",
            "def add_track_to(cam, look):",
            "    for c in list(cam.constraints): cam.constraints.remove(c)",
            "    t = cam.constraints.new('TRACK_TO')",
            "    t.target = look; t.track_axis = 'TRACK_NEGATIVE_Z'; t.up_axis = 'UP_Y'",
            "",
            "def place_static(cam, subject, shot_type, composition, angle_deg):",
            "    bpy.context.view_layer.update()",
            "    loc  = get_subject_loc(subject)",
            "    dist = SHOT_DISTANCES.get(shot_type, 4.0)",
            "    hoff = SHOT_H_OFFSETS.get(shot_type, 0.0)",
            "    ar   = radians(angle_deg)",
            "    xoff = COMPOSITION_FACTOR * dist * (",
            "        1.0 if composition=='RIGHT' else -1.0 if composition=='LEFT' else 0.0)",
            "    ox, oy = cos(ar), sin(ar)",
            "    cam.location = (dist*sin(ar) + xoff*ox + loc.x,",
            "                    -dist*cos(ar) + xoff*oy + loc.y,",
            "                    TARGET_HEIGHT + hoff + loc.z)",
            "",
            "def set_bezier(cam, start_f, end_f):",
            "    if cam.animation_data and cam.animation_data.action:",
            "        for fc in cam.animation_data.action.fcurves:",
            "            for kp in fc.keyframe_points:",
            "                kp.interpolation = 'BEZIER'",
            "",
            "# Evaluate scene at frame 1 so NLA + manual repositioning are applied",
            "scene.frame_set(1)",
            "bpy.context.view_layer.update()",
            "",
            "# Clear old markers",
            "for m in list(scene.timeline_markers): scene.timeline_markers.remove(m)",
            "print('🎥 Building camera shots...')",
        ]

        for i, shot in enumerate(shots):
            name        = shot["name"]
            subject     = shot["subject"]
            shot_type   = shot.get("shot_type", "MS")
            composition = shot.get("composition", "CENTER")
            angle       = shot.get("angle", 0)
            movement    = shot.get("movement", "STATIC")
            start_s     = shot.get("start_second", 0)
            end_s       = shot.get("end_second", 10)
            sf          = int(start_s * fps) + 1
            ef          = int(end_s   * fps)
            subj_repr   = repr(subject)
            look_name   = f"LookAt_{name}"

            lines += [
                "",
                f"# ── {name} ──",
                f"_subj_{i} = {subj_repr}",
                f"_cd_{i}   = bpy.data.cameras.new('{name}')",
                f"_cd_{i}.lens = 35",
                f"_cam_{i}  = bpy.data.objects.new('{name}', _cd_{i})",
                f"bpy.context.collection.objects.link(_cam_{i})",
                f"_look_{i} = get_look_at(_subj_{i}, '{look_name}')",
                f"bpy.context.view_layer.update()",
            ]

            if movement == "STATIC":
                lines += [
                    f"place_static(_cam_{i}, _subj_{i}, '{shot_type}', '{composition}', {angle})",
                    f"add_track_to(_cam_{i}, _look_{i})",
                ]

            elif movement in ("PUSH_IN", "PUSH_OUT"):
                sd = shot.get("start_distance", self.SHOT_DISTANCES.get(shot_type, 4.0))
                ed = shot.get("end_distance",   self.SHOT_DISTANCES.get(shot_type, 4.0) * 0.5)
                ho = self.SHOT_HEIGHT_OFFSETS.get(shot_type, 0.0)
                lines += [
                    f"_ar_{i}  = radians({angle})",
                    f"_sl_{i}  = get_subject_loc(_subj_{i})",
                    f"_sx_{i}  = {sd} * sin(_ar_{i}) + _sl_{i}.x",
                    f"_sy_{i}  = -{sd} * cos(_ar_{i}) + _sl_{i}.y",
                    f"_ex_{i}  = {ed} * sin(_ar_{i}) + _sl_{i}.x",
                    f"_ey_{i}  = -{ed} * cos(_ar_{i}) + _sl_{i}.y",
                    f"_cz_{i}  = TARGET_HEIGHT + {ho} + _sl_{i}.z",
                    f"add_track_to(_cam_{i}, _look_{i})",
                    f"_cam_{i}.location = (_sx_{i}, _sy_{i}, _cz_{i})",
                    f"_cam_{i}.keyframe_insert(data_path='location', frame={sf})",
                    f"_cam_{i}.location = (_ex_{i}, _ey_{i}, _cz_{i})",
                    f"_cam_{i}.keyframe_insert(data_path='location', frame={ef})",
                    f"set_bezier(_cam_{i}, {sf}, {ef})",
                ]

            elif movement == "TRUCK":
                tx0 = shot.get("truck_start_x", -5.0)
                tx1 = shot.get("truck_end_x",    0.0)
                td  = shot.get("truck_depth",     4.0)
                th  = shot.get("truck_height",    self.TARGET_HEIGHT)
                lines += [
                    f"_sl_{i} = get_subject_loc(_subj_{i})",
                    f"add_track_to(_cam_{i}, _look_{i})",
                    f"_cam_{i}.location = ({tx0} + _sl_{i}.x, {td} + _sl_{i}.y, {th})",
                    f"_cam_{i}.keyframe_insert(data_path='location', frame={sf})",
                    f"_cam_{i}.location = ({tx1} + _sl_{i}.x, {td} + _sl_{i}.y, {th})",
                    f"_cam_{i}.keyframe_insert(data_path='location', frame={ef})",
                    f"set_bezier(_cam_{i}, {sf}, {ef})",
                ]

            elif movement == "PAN":
                pan0 = shot.get("pan_start_angle", 0.0)
                pan1 = shot.get("pan_end_angle",   90.0)
                dist = self.SHOT_DISTANCES.get(shot_type, 4.0)
                ho   = self.SHOT_HEIGHT_OFFSETS.get(shot_type, 0.0)
                lines += [
                    f"_sl_{i}  = get_subject_loc(_subj_{i})",
                    f"# Pan: camera stays at mid-angle position, look-at animates",
                    f"_ar0_{i} = radians({pan0})",
                    f"_ar1_{i} = radians({pan1})",
                    f"_amid_{i}= ({pan0}+{pan1})/2.0",
                    f"_arM_{i} = radians(_amid_{i})",
                    f"_cam_{i}.location = ({dist}*sin(_arM_{i})+_sl_{i}.x,",
                    f"                     -{dist}*cos(_arM_{i})+_sl_{i}.y,",
                    f"                     TARGET_HEIGHT + {ho} + _sl_{i}.z)",
                    # Keyframe look-at empty from pan_start to pan_end
                    f"_look_{i}.parent = None",
                    f"_look_{i}.location = (_sl_{i}.x + {dist}*sin(_ar0_{i})*0.01,",
                    f"                       _sl_{i}.y - {dist}*cos(_ar0_{i})*0.01,",
                    f"                       _sl_{i}.z + TARGET_HEIGHT)",
                    f"_look_{i}.keyframe_insert(data_path='location', frame={sf})",
                    f"_look_{i}.location = (_sl_{i}.x + {dist}*sin(_ar1_{i})*0.01,",
                    f"                       _sl_{i}.y - {dist}*cos(_ar1_{i})*0.01,",
                    f"                       _sl_{i}.z + TARGET_HEIGHT)",
                    f"_look_{i}.keyframe_insert(data_path='location', frame={ef})",
                    f"add_track_to(_cam_{i}, _look_{i})",
                    f"set_bezier(_cam_{i}, {sf}, {ef})",
                ]

            elif movement == "TILT":
                tz0 = shot.get("tilt_start_z", self.TARGET_HEIGHT)
                tz1 = shot.get("tilt_end_z",   0.5)
                dist = self.SHOT_DISTANCES.get(shot_type, 4.0)
                lines += [
                    f"_sl_{i}  = get_subject_loc(_subj_{i})",
                    f"_ar_{i}  = radians({angle})",
                    f"_cam_{i}.location = ({dist}*sin(_ar_{i})+_sl_{i}.x,",
                    f"                     -{dist}*cos(_ar_{i})+_sl_{i}.y,",
                    f"                     ({tz0}+{tz1})/2.0)",
                    # Look-at empty animates from tilt_start_z to tilt_end_z
                    f"_look_{i}.parent = None",
                    f"_look_{i}.location = (_sl_{i}.x, _sl_{i}.y, {tz0})",
                    f"_look_{i}.keyframe_insert(data_path='location', frame={sf})",
                    f"_look_{i}.location = (_sl_{i}.x, _sl_{i}.y, {tz1})",
                    f"_look_{i}.keyframe_insert(data_path='location', frame={ef})",
                    f"add_track_to(_cam_{i}, _look_{i})",
                    f"set_bezier(_cam_{i}, {sf}, {ef})",
                ]

            elif movement == "CRANE":
                cz0  = shot.get("crane_start_z", 1.0)
                cz1  = shot.get("crane_end_z",   self.TARGET_HEIGHT)
                dist = self.SHOT_DISTANCES.get(shot_type, 4.0)
                lines += [
                    f"_sl_{i} = get_subject_loc(_subj_{i})",
                    f"_ar_{i} = radians({angle})",
                    f"add_track_to(_cam_{i}, _look_{i})",
                    f"_cam_{i}.location = ({dist}*sin(_ar_{i})+_sl_{i}.x,",
                    f"                     -{dist}*cos(_ar_{i})+_sl_{i}.y, {cz0})",
                    f"_cam_{i}.keyframe_insert(data_path='location', frame={sf})",
                    f"_cam_{i}.location = ({dist}*sin(_ar_{i})+_sl_{i}.x,",
                    f"                     -{dist}*cos(_ar_{i})+_sl_{i}.y, {cz1})",
                    f"_cam_{i}.keyframe_insert(data_path='location', frame={ef})",
                    f"set_bezier(_cam_{i}, {sf}, {ef})",
                ]

            elif movement == "ORBIT":
                radius    = shot.get("orbit_radius",      5.0)
                orb_start = shot.get("orbit_start_angle", 0.0)
                orb_end   = shot.get("orbit_end_angle",   90.0)
                orb_h     = shot.get("orbit_height",      0.0)
                orb_dur   = ef - sf
                path_name = f"{name}_OrbitPath"
                lines += [
                    f"_lw_{i}  = _look_{i}.matrix_world.translation.copy() if _look_{i}.parent else _look_{i}.location.copy()",
                    f"_oc_{i}  = _lw_{i}.copy(); _oc_{i}.z += {orb_h}",
                    f"_op_{i}  = bpy.data.objects.get('{path_name}')",
                    f"if _op_{i}: _ocd = _op_{i}.data; bpy.data.objects.remove(_op_{i}, do_unlink=True); bpy.data.curves.remove(_ocd) if _ocd and _ocd.users==0 else None",
                    f"_ocd_{i} = bpy.data.curves.new('{path_name}', 'CURVE')",
                    f"_ocd_{i}.dimensions = '3D'",
                    f"_osp_{i} = _ocd_{i}.splines.new('BEZIER'); _osp_{i}.use_cyclic_u = True",
                    f"_r_{i} = {radius}",
                    f"_pts_{i} = [(_r_{i},0,0),(0,_r_{i},0),(-_r_{i},0,0),(0,-_r_{i},0)]",
                    f"_osp_{i}.bezier_points.add(3)",
                    f"for _pi,(px,py,pz) in enumerate(_pts_{i}):",
                    f"    _bp = _osp_{i}.bezier_points[_pi]",
                    f"    _bp.co = (_oc_{i}.x+px, _oc_{i}.y+py, _oc_{i}.z+pz)",
                    f"    _bp.handle_left_type = _bp.handle_right_type = 'AUTO'",
                    f"_opo_{i} = bpy.data.objects.new('{path_name}', _ocd_{i})",
                    f"bpy.context.collection.objects.link(_opo_{i})",
                    f"_ocd_{i}.use_path = True; _ocd_{i}.path_duration = {orb_dur}",
                    f"_opo_{i}.hide_select = _opo_{i}.hide_render = True",
                    f"_cam_{i}.location = (0,0,0)",
                    f"_fp_{i} = _cam_{i}.constraints.new('FOLLOW_PATH')",
                    f"_fp_{i}.target = _opo_{i}; _fp_{i}.use_fixed_location = True; _fp_{i}.use_curve_follow = False",
                    f"_fp_{i}.offset_factor = {orb_start}/360.0",
                    f"_fn_{i} = _fp_{i}.name",
                    f"_cam_{i}.keyframe_insert(data_path='constraints[\"'+_fn_{i}+'\"].offset_factor', frame={sf})",
                    f"_fp_{i}.offset_factor = {orb_end}/360.0",
                    f"_cam_{i}.keyframe_insert(data_path='constraints[\"'+_fn_{i}+'\"].offset_factor', frame={ef})",
                    f"add_track_to(_cam_{i}, _look_{i})",
                ]

            lines += [
                f"_mk_{i} = scene.timeline_markers.new('{name}', frame={sf})",
                f"_mk_{i}.camera = _cam_{i}",
                f"print(f'  ✅ {name}: {shot_type} | {movement} | frames {sf}-{ef}')",
            ]

        lines += [
            "",
            "scene.camera = _cam_0",
            f"bpy.ops.wm.save_as_mainfile(filepath=r'{output_blend}')",
            "print('✅ Cameras saved.')",
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Pipeline run                                                         #
    # ------------------------------------------------------------------ #

    def run(
        self,
        screenplay:    str | None,
        scene_json:    dict,
        blend_in:      str,
        blend_out:     str  = None,
        shots_json_in: str  = None,
        cameras_only:  bool = False,
    ) -> bool:
        blend_in  = str(Path(blend_in).absolute())
        blend_out = blend_out or blend_in.replace(".blend", "_cameras.blend")

        if shots_json_in:
            print(f"📂 Loading camera shots from {shots_json_in}")
            with open(shots_json_in) as f:
                shots_data = json.load(f)
        else:
            shots_data = self.generate_shots_json(screenplay, scene_json)
            if not shots_data:
                return False
            shots_json_out = blend_out.replace(".blend", "_cameras.json")
            with open(shots_json_out, "w") as f:
                json.dump(shots_data, f, indent=2)
            print(f"💾 Camera shots saved: {shots_json_out}")

        if cameras_only:
            print("   (--cameras-only: stopping here)")
            return True

        blender_script = self._generate_blender_camera_script(
            shots_data, scene_json, blend_out
        )
        script_path = str(self.script_dir / "_camera_setup_script.py")
        debug_path  = str(self.script_dir / "debug_camera_script.py")
        for p in (script_path, debug_path):
            with open(p, "w") as f:
                f.write(blender_script)
        print(f"🐛 Debug camera script: {debug_path}")

        blender_path = os.environ.get("BLENDER_PATH") or {
            "darwin":  "/Applications/Blender.app/Contents/MacOS/Blender",
            "linux":   "blender",
            "win32":   r"C:\Program Files\Blender Foundation\Blender\blender.exe",
        }.get(sys.platform, "blender")
        print(f"\n🎬 Adding cameras to: {blend_in}")
        try:
            result = subprocess.run(
                [blender_path, "--background", blend_in, "--python", script_path],
                capture_output=True, text=True, timeout=300,
            )
            if os.path.exists(script_path):
                os.remove(script_path)
            if result.returncode == 0:
                print(f"✅ Done: {blend_out}")
                return True
            print(f"❌ Blender failed:\n{result.stderr[-1500:]}")
            return False
        except subprocess.TimeoutExpired:
            print("⏰ Blender timed out.")
            return False
        except Exception as e:
            print(f"❌ Error: {e}")
            return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 2 — Add multi-camera setup to a Phase 1 Blender scene.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 camera_setup.py --blend scene.blend --json scene.json --file script.txt
  python3 camera_setup.py --blend scene.blend --json scene.json --backend anthropic
  python3 camera_setup.py --blend scene.blend --json scene.json --cameras-json shots.json
        """,
    )
    parser.add_argument("screenplay",     nargs="?",            help="Inline screenplay text")
    parser.add_argument("--file",   "-f",                       help="Screenplay .txt / .pdf")
    parser.add_argument("--blend",  "-b", required=True,        help="Input .blend (Phase 1 output)")
    parser.add_argument("--json",   "-j", required=True,        help="Phase 1 scene .json")
    parser.add_argument("--output", "-o",                       help="Output .blend path")
    parser.add_argument("--cameras-json",                       help="Load pre-generated camera shots JSON")
    parser.add_argument("--cameras-only", action="store_true",  help="Stop after saving camera JSON")
    parser.add_argument("--backend", "-B", default="auto",
                        help="LLM backend: groq | anthropic | gemini | openai  (default: auto)")
    parser.add_argument("--model",         default=None,        help="Override model name")
    args = parser.parse_args()

    if args.file:
        screenplay = Path(args.file).read_text(encoding="utf-8", errors="replace")
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
        screenplay = None

    try:
        with open(args.json) as f:
            scene_json = json.load(f)
    except FileNotFoundError:
        print(f"❌ Scene JSON not found: {args.json}")
        sys.exit(1)

    print("=" * 60)
    print("🎬  CAMERA SETUP  (Phase 2)")
    print("=" * 60)
    print(f"Blend in : {args.blend}")
    print(f"Scene    : {scene_json.get('scene', {}).get('name', '?')}")
    print(f"Backend  : {args.backend}")
    print()

    ok = CameraSetup(backend=args.backend, model=args.model).run(
        screenplay    = screenplay,
        scene_json    = scene_json,
        blend_in      = args.blend,
        blend_out     = args.output,
        shots_json_in = args.cameras_json,
        cameras_only  = args.cameras_only,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
