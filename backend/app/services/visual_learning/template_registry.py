"""
Single-source-of-truth loader for the HyperFrames template registry.

The registry itself lives at hyperframes_engine/shared/template-registry.json
(plain JSON so the Node engine can require() it and this module can json.load()
it with zero new dependencies on either side). Do not hardcode template ids,
"best for" descriptions, or selection constraints anywhere else - edit the JSON
file and every consumer (LLM prompt text, post-hoc audit/repair pass) updates
automatically.
"""
import os
import json
import math
import logging

logger = logging.getLogger(__name__)


def _hex_to_rgb_str(hex_color: str) -> str:
    """'#ff6347' -> '255,99,71', for building rgba() fill strings."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "148,163,184"
    try:
        return f"{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)}"
    except ValueError:
        return "148,163,184"


_BRANCH_COLORS = ["#ff6347", "#22c55e", "#f59e0b", "#38bdf8", "#a78bfa", "#f472b6"]


def generate_branching_diagram(params: dict) -> list:
    """
    Procedural 'illustrated_scene' elements generator for any one-thing-splits-
    into-several concept: lungs/bronchi/alveoli, a river and its tributaries, a
    family tree, an org chart, a neuron's dendrites, a blood vessel network.
    Same shape, same code, any subject - this is the first of the diagram
    "primitives": instead of a hand-authored diagram per topic (doesn't scale)
    or the LLM inventing raw coordinates per topic (unreliable, see the
    original respiratory-system bug), the LLM supplies structured data (what
    are the branches, do they end in a cluster or a single node) and this
    function computes correct, guaranteed-non-overlapping geometry - the same
    principle concept_diagram's angle math and cycle_template's orbit math
    already use successfully.

    params:
      source: {"label": str, "icon": str}          - the trunk/origin
      branches: [{"label": str, "cluster": bool}]   - cluster=true draws a
                                                       small grape-like circle
                                                       cluster at the tip
                                                       (e.g. alveoli); false
                                                       draws a single labeled
                                                       node (e.g. a family
                                                       member, a tributary)
    """
    source = params.get("source") or {}
    branches = params.get("branches") or []
    if not branches:
        return []
    n = len(branches)

    cx, cy = 640, 300
    trunk_top_y = 110
    radius = 230
    spread_deg = min(150, 34 + n * 20)
    start_angle = -spread_deg / 2
    step = spread_deg / (n - 1) if n > 1 else 0

    elements = [
        {
            "type": "rect", "x": cx - 22, "y": trunk_top_y, "width": 44, "height": cy - trunk_top_y,
            "stroke_color": "#94a3b8", "fill": "rgba(148,163,184,0.15)",
            "label": source.get("label", ""), "position": {"x": cx - 170, "y": (trunk_top_y + cy) / 2}
        },
        {
            "type": "path", "path_data": f"M {cx} {trunk_top_y - 30} L {cx} {cy}",
            "stroke_color": "#3b82f6", "stroke_width": 3, "dash_array": "8 6"
        },
    ]

    for i, br in enumerate(branches):
        angle_rad = math.radians(start_angle + step * i)
        ex = cx + radius * math.sin(angle_rad)
        ey = cy + radius * math.cos(angle_rad)
        mx = cx + (radius * 0.55) * math.sin(angle_rad) + (28 if i % 2 == 0 else -28)
        my = cy + (radius * 0.45) * math.cos(angle_rad)
        color = _BRANCH_COLORS[i % len(_BRANCH_COLORS)]
        rgb = _hex_to_rgb_str(color)

        elements.append({
            "type": "path", "path_data": f"M {cx} {cy} Q {mx} {my} {ex} {ey}",
            "stroke_color": "#94a3b8", "stroke_width": 9, "fill": "none"
        })

        if br.get("cluster"):
            for j, (dx, dy, r) in enumerate([(-14, -6, 13), (10, -10, 11), (-8, 12, 12), (12, 10, 10)]):
                el = {"type": "circle", "cx": ex + dx, "cy": ey + dy, "r": r,
                      "stroke_color": color, "fill": f"rgba({rgb},0.5)"}
                if j == 0:
                    el["label"] = br.get("label", "")
                    el["position"] = {"x": ex, "y": ey + 55}
                elements.append(el)
        else:
            r = 46
            elements.append({
                "type": "circle", "cx": ex, "cy": ey, "r": r,
                "stroke_color": color, "fill": f"rgba({rgb},0.35)",
                "label": br.get("label", ""), "position": {"x": ex, "y": ey + r + 22}
            })

    return elements


def generate_container_flow_diagram(params: dict) -> list:
    """
    Second diagram primitive: a labeled container/vessel with items flowing
    in on the left and out on the right - the water cycle's ocean, the
    digestive tract as a through-pipe, blood through a heart chamber, money
    through an economy, migration in/out of a region. Same shape, same code,
    any subject - same principle as generate_branching_diagram.

    params:
      container: {"label": str}                 - the central vessel
      inflows: [{"label": str}]                  - items entering (left side)
      outflows: [{"label": str}]                 - items leaving (right side)
    """
    container = params.get("container") or {}
    inflows = params.get("inflows") or []
    outflows = params.get("outflows") or []
    if not inflows and not outflows:
        return []

    cx, cy = 640, 360
    cw, ch = 320, 220

    elements = [{
        "type": "rect", "x": cx - cw / 2, "y": cy - ch / 2, "width": cw, "height": ch, "rx": 24,
        "stroke_color": "#38bdf8", "fill": "rgba(56,189,248,0.18)",
        "label": container.get("label", ""), "position": {"x": cx, "y": cy - ch / 2 - 20}
    }]

    # Texture accents inside the container so it doesn't read as a bare box -
    # same principle as respiratory_system's alveoli-cluster circles, applied
    # generically: a handful of small unlabeled circles suggesting contents
    # being processed inside the vessel (blood cells, coins, particles...).
    for dx, dy, r in [(-70, -50, 10), (40, -30, 8), (-20, 40, 9), (75, 55, 7)]:
        elements.append({
            "type": "circle", "cx": cx + dx, "cy": cy + dy, "r": r,
            "stroke_color": "#38bdf8", "fill": "rgba(56,189,248,0.4)"
        })

    def _spread_y(n, top=160, bottom=560):
        if n == 1:
            return [(top + bottom) / 2]
        step = (bottom - top) / (n - 1)
        return [top + step * i for i in range(n)]

    colors = ["#22c55e", "#f59e0b", "#a78bfa", "#f472b6", "#38bdf8", "#ff6347"]

    in_x_start, in_x_end = 140, cx - cw / 2 - 20
    for i, (item, y) in enumerate(zip(inflows, _spread_y(len(inflows)))):
        color = colors[i % len(colors)]
        elements.append({
            "type": "path", "path_data": f"M {in_x_start} {y} L {in_x_end} {y}",
            "stroke_color": color, "stroke_width": 3, "dash_array": "8 6",
            "label": item.get("label", ""), "position": {"x": (in_x_start + in_x_end) / 2, "y": y - 16}
        })

    out_x_start, out_x_end = cx + cw / 2 + 20, 1140
    for i, (item, y) in enumerate(zip(outflows, _spread_y(len(outflows)))):
        color = colors[(i + 2) % len(colors)]
        elements.append({
            "type": "path", "path_data": f"M {out_x_start} {y} L {out_x_end} {y}",
            "stroke_color": color, "stroke_width": 3, "dash_array": "8 6",
            "label": item.get("label", ""), "position": {"x": (out_x_start + out_x_end) / 2, "y": y - 16}
        })

    return elements


def generate_paired_organ_diagram(params: dict) -> list:
    """
    Third diagram primitive: two mirrored organ-shaped blobs connected to a
    central stem - lungs, kidneys, ears, brain hemispheres. Same shape, same
    code, any specific paired organ - only the labels change.

    params:
      stem: {"label": str}    - the connecting structure (trachea, spinal cord, brain stem)
      left: {"label": str}    - left-side organ label
      right: {"label": str}   - right-side organ label
    """
    stem = params.get("stem") or {}
    left = params.get("left") or {}
    right = params.get("right") or {}
    if not left and not right:
        return []

    cx = 640
    elements = [{
        "type": "rect", "x": cx - 25, "y": 120, "width": 50, "height": 160,
        "stroke_color": "#94a3b8", "fill": "rgba(148,163,184,0.15)",
        "label": stem.get("label", ""), "position": {"x": cx - 190, "y": 200}
    }]

    elements.append({"type": "path", "path_data": f"M {cx-20} 280 Q {cx-140} 320 {cx-220} 360", "stroke_color": "#94a3b8", "stroke_width": 9, "fill": "none"})
    elements.append({"type": "path", "path_data": f"M {cx+20} 280 Q {cx+140} 320 {cx+220} 360", "stroke_color": "#94a3b8", "stroke_width": 9, "fill": "none"})

    left_path = "M 420 360 C 340 360 280 440 290 520 C 300 590 380 620 440 590 C 480 570 480 460 500 400 C 510 375 500 365 420 360 Z"
    right_path = "M 860 360 C 940 360 1000 440 990 520 C 980 590 900 620 840 590 C 800 570 800 460 780 400 C 770 375 780 365 860 360 Z"

    elements.append({"type": "path", "path_data": left_path, "stroke_color": "#ff6347", "stroke_width": 2, "fill": "rgba(255,99,71,0.35)", "label": left.get("label", ""), "position": {"x": 360, "y": 630}})
    # Texture cluster (3 accent circles, matching respiratory_system's alveoli
    # density) instead of a single dot, so each organ blob doesn't read as flat.
    for dx, dy, r in [(0, 0, 12), (22, -6, 10), (-6, 22, 9)]:
        elements.append({"type": "circle", "cx": 400 + dx, "cy": 460 + dy, "r": r, "stroke_color": "#32cd32", "fill": "rgba(50,205,50,0.5)"})

    elements.append({"type": "path", "path_data": right_path, "stroke_color": "#ff6347", "stroke_width": 2, "fill": "rgba(255,99,71,0.35)", "label": right.get("label", ""), "position": {"x": 920, "y": 630}})
    for dx, dy, r in [(0, 0, 12), (-22, -6, 10), (6, 22, 9)]:
        elements.append({"type": "circle", "cx": 880 + dx, "cy": 460 + dy, "r": r, "stroke_color": "#32cd32", "fill": "rgba(50,205,50,0.5)"})

    return elements


def generate_stacked_layers_diagram(params: dict) -> list:
    """
    Fourth diagram primitive: stacked layers - Earth's layers, atmosphere,
    a social/government hierarchy, geological strata. Two geometry modes:
    concentric rings (core_out=true, for "center outward" concepts like
    Earth's core/mantle/crust) or flat horizontal bands (default, for
    top-to-bottom hierarchies like tiers of government). Same shape, same
    code, any subject - only labels and mode change.

    params:
      layers: [{"label": str}]  - ordered innermost-to-outermost (core_out)
                                   or top-to-bottom (flat bands)
      core_out: bool             - true for concentric rings, false/omitted
                                   for flat horizontal bands
    """
    layers = params.get("layers") or []
    n = len(layers)
    if n == 0:
        return []
    colors = ["#ff6347", "#f59e0b", "#facc15", "#22c55e", "#38bdf8", "#a78bfa"]

    if params.get("core_out"):
        cx, cy = 640, 360
        max_r = 260
        elements = []
        for i in range(n - 1, -1, -1):
            r = max_r * (i + 1) / n
            color = colors[i % len(colors)]
            elements.append({
                "type": "circle", "cx": cx, "cy": cy, "r": r,
                "stroke_color": color, "fill": f"rgba({_hex_to_rgb_str(color)},0.55)",
                "label": layers[i].get("label", ""),
                "position": {"x": cx + r * 0.7 + 20, "y": cy - r * 0.7}
            })
        return elements

    band_h = 400 / n
    top = 160
    elements = []
    for i, layer in enumerate(layers):
        color = colors[i % len(colors)]
        y = top + i * band_h
        elements.append({
            "type": "rect", "x": 300, "y": y, "width": 680, "height": band_h,
            "stroke_color": color, "fill": f"rgba({_hex_to_rgb_str(color)},0.3)",
            "label": layer.get("label", ""), "position": {"x": 250, "y": y + band_h / 2}
        })
        # Texture accent (a few short tick marks along the band), same
        # principle as respiratory_system's tracheal-ring lines - stops a
        # flat-band diagram from reading as bare colored rectangles.
        tick_y = y + band_h / 2
        for tick_x in (350, 500, 650, 800, 900):
            elements.append({
                "type": "line", "x1": tick_x, "y1": tick_y - band_h * 0.18,
                "x2": tick_x, "y2": tick_y + band_h * 0.18,
                "stroke_color": color, "stroke_width": 1.5
            })
    return elements


def generate_enclosure_diagram(params: dict) -> list:
    """
    Fifth diagram primitive: a boundary shape containing several labeled
    parts inside it - a parliament building with its roles, a cell with its
    organelles, a country with its resources, a factory with its
    departments. Same shape, same code, any subject.

    params:
      boundary: {"label": str}   - the containing structure
      contents: [{"label": str}] - items arranged inside the boundary (grid layout)
    """
    boundary = params.get("boundary") or {}
    contents = params.get("contents") or []
    n = len(contents)
    if n == 0:
        return []

    bx, by, bw, bh = 260, 140, 760, 440
    elements = [{
        "type": "rect", "x": bx, "y": by, "width": bw, "height": bh, "rx": 28,
        "stroke_color": "#a78bfa", "stroke_width": 3, "fill": "rgba(167,139,250,0.08)",
        "label": boundary.get("label", ""), "position": {"x": bx + bw / 2, "y": by - 20}
    }]

    cols = 3 if n > 4 else min(2, n)
    rows = -(-n // cols)
    cell_w = (bw - 80) / cols
    cell_h = (bh - 80) / rows
    colors = ["#38bdf8", "#22c55e", "#f59e0b", "#ff6347", "#facc15", "#f472b6"]

    for i, item in enumerate(contents):
        row, col = divmod(i, cols)
        cx = bx + 40 + cell_w * (col + 0.5)
        cy = by + 40 + cell_h * (row + 0.5)
        color = colors[i % len(colors)]
        r = min(cell_w, cell_h) * 0.28
        elements.append({
            "type": "circle", "cx": cx, "cy": cy, "r": r,
            "stroke_color": color, "fill": f"rgba({_hex_to_rgb_str(color)},0.4)",
            "label": item.get("label", ""), "position": {"x": cx, "y": cy + r + 22}
        })
        # Inner accent ring (a smaller concentric circle) so each content
        # item reads as a real structure with detail, not a flat dot - same
        # texture principle used elsewhere (respiratory's alveoli clusters,
        # paired_organ's accent circles).
        elements.append({
            "type": "circle", "cx": cx, "cy": cy, "r": r * 0.45,
            "stroke_color": color, "fill": f"rgba({_hex_to_rgb_str(color)},0.7)"
        })

    return elements


def generate_node_network_diagram(params: dict) -> list:
    """
    Sixth diagram primitive: nodes connected by lines in a non-hierarchical
    graph - a circuit, a food web, a trade network. Same shape, same code,
    any subject.

    params:
      nodes: [{"label": str}]         - arranged evenly around a circle
      connections: [[i, j], ...]      - 0-based index pairs to connect
    """
    nodes = params.get("nodes") or []
    connections = params.get("connections") or []
    n = len(nodes)
    if n == 0:
        return []
    cx, cy, radius = 640, 360, 220
    positions = []
    for i in range(n):
        angle = 2 * math.pi * i / n - math.pi / 2
        positions.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))

    elements = []
    for pair in connections:
        if len(pair) != 2:
            continue
        i, j = pair
        if 0 <= i < n and 0 <= j < n:
            x1, y1 = positions[i]
            x2, y2 = positions[j]
            elements.append({"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "stroke_color": "#94a3b8", "stroke_width": 2})

    colors = ["#38bdf8", "#22c55e", "#f59e0b", "#ff6347", "#a78bfa", "#f472b6"]
    for i, (node, (x, y)) in enumerate(zip(nodes, positions)):
        color = colors[i % len(colors)]
        elements.append({
            "type": "circle", "cx": x, "cy": y, "r": 44,
            "stroke_color": color, "fill": f"rgba({_hex_to_rgb_str(color)},0.4)",
            "label": node.get("label", ""), "position": {"x": x, "y": y + 44 + 22}
        })
    return elements


def generate_radiating_center_diagram(params: dict) -> list:
    """
    Seventh diagram primitive: a central circle with items radiating outward
    on spokes - an atom (nucleus + electrons), a solar system (sun +
    planets), a leader with departments reporting in. Same shape, same code,
    any subject.

    params:
      center: {"label": str}          - the central element
      satellites: [{"label": str}]    - arranged evenly around the center
    """
    center = params.get("center") or {}
    satellites = params.get("satellites") or []
    n = len(satellites)
    if n == 0:
        return []
    cx, cy = 640, 360
    elements = [{
        "type": "circle", "cx": cx, "cy": cy, "r": 60,
        "stroke_color": "#f59e0b", "fill": "rgba(245,158,11,0.4)",
        "label": center.get("label", ""), "position": {"x": cx, "y": cy + 60 + 26}
    }]
    # Texture inside the center (e.g. protons/neutrons packed in a nucleus,
    # not just a flat circle) - same alveoli-cluster principle used elsewhere.
    for dx, dy, r in [(-14, -8, 11), (12, -12, 10), (-8, 14, 10), (14, 10, 9)]:
        elements.append({
            "type": "circle", "cx": cx + dx, "cy": cy + dy, "r": r,
            "stroke_color": "#fbbf24", "fill": "rgba(251,191,36,0.6)"
        })
    radius = 220
    # Shared dashed orbit ring behind the satellites, so they read as
    # objects on a real orbit path rather than just dots at the end of
    # straight spokes (all satellites share this radius already).
    elements.append({
        "type": "circle", "cx": cx, "cy": cy, "r": radius,
        "stroke_color": "#94a3b8", "stroke_width": 1.5, "fill": "none", "dash_array": "3 5"
    })
    colors = ["#38bdf8", "#22c55e", "#a78bfa", "#ff6347", "#f472b6", "#facc15"]
    for i, sat in enumerate(satellites):
        angle = 2 * math.pi * i / n
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        color = colors[i % len(colors)]
        elements.append({"type": "line", "x1": cx, "y1": cy, "x2": x, "y2": y, "stroke_color": color, "stroke_width": 2, "dash_array": "4 3"})
        elements.append({
            "type": "circle", "cx": x, "cy": y, "r": 30,
            "stroke_color": color, "fill": f"rgba({_hex_to_rgb_str(color)},0.4)",
            "label": sat.get("label", ""), "position": {"x": x, "y": y + 30 + 20}
        })
    return elements


DIAGRAM_PRIMITIVES = {
    "branching": generate_branching_diagram,
    "container_flow": generate_container_flow_diagram,
    "paired_organ": generate_paired_organ_diagram,
    "stacked_layers": generate_stacked_layers_diagram,
    "enclosure": generate_enclosure_diagram,
    "node_network": generate_node_network_diagram,
    "radiating_center": generate_radiating_center_diagram,
}


# Topic keyword gates for the concept_diagram-donor backstops below. These are
# deliberately narrow (organ *pairs* only, container *structures* only) so a
# generic 2-6-leaf concept_diagram doesn't get mis-converted into the wrong
# specific shape - branching remains the correct default for everything else.
ORGAN_PAIR_KEYWORDS = [
    "lung", "kidney", "ear", "eye", "hand", "arm", "leg", "ovary", "ovaries",
    "testis", "testes", "nostril", "hemisphere", "tonsil", "bicep", "lobe",
    "adrenal gland", "eardrum", "cerebral hemisphere",
]
ENCLOSURE_KEYWORDS = [
    "cell", "parliament", "government building", "factory", "country",
    "ecosystem", "habitat", "biome", "classroom", "market", "household",
    "organelle", "assembly", "legislature", "chamber of commerce",
]
NETWORK_KEYWORDS = [
    "circuit", "food web", "food chain", "trade network", "computer network",
    "supply chain", "communication network", "power grid", "neural network",
]


def _concept_diagram_donor_text(clip: dict, data: dict) -> str:
    central = data.get("central_node") or {}
    return " ".join(str(t) for t in (
        central.get("text"), data.get("title"), clip.get("purpose"), clip.get("teacher_script"),
    ) if t).lower()


def force_paired_organ_primitive(clips: list, log=None) -> list:
    """
    Same reliability-gap pattern as force_branching_primitive, but for the
    paired_organ primitive: if the LLM picked concept_diagram (central_node +
    EXACTLY 2 leaf_nodes) for a topic that names a paired body structure
    (lungs, kidneys, ears, brain hemispheres...), convert it to
    illustrated_scene + paired_organ - reusing the LLM's own central_node as
    the stem and the two leaf_nodes as left/right. Must run before
    force_branching_primitive (which would otherwise silently claim the same
    2-leaf scene first, since it has no topic gate of its own) - this is the
    fix for the observed bug where brain hemispheres etc. always rendered as
    generic branching circles instead of organ blobs.
    """
    log = log or (lambda msg: None)
    if any(c.get("_curated_diagram_id") or c.get("_primitive_shape") for c in clips):
        return clips

    for idx, clip in enumerate(clips):
        if clip.get("template_id") != "concept_diagram":
            continue
        data = clip.get("template_data") or {}
        leaf_nodes = data.get("leaf_nodes") or []
        if len(leaf_nodes) != 2:
            continue
        if not any(kw in _concept_diagram_donor_text(clip, data) for kw in ORGAN_PAIR_KEYWORDS):
            continue
        left_label = leaf_nodes[0].get("text", "")
        right_label = leaf_nodes[1].get("text", "")
        if not left_label or not right_label:
            continue
        central = data.get("central_node") or {}
        generated = generate_paired_organ_diagram({
            "stem": {"label": central.get("text", "")},
            "left": {"label": left_label},
            "right": {"label": right_label},
        })
        if not generated:
            continue
        clip["template_id"] = "illustrated_scene"
        clip["beat_shape"] = "spatial"
        clip["template_data"] = {
            "title": data.get("title") or clip.get("purpose") or "",
            "animation_action": "rise",
            "elements": generated,
            "primitive_shape": "paired_organ",
        }
        clip["_primitive_shape"] = "paired_organ"
        log(f"   [AUDIT REPAIR] Scene {idx + 1} force-converted from concept_diagram to illustrated_scene + paired_organ primitive (topic keyword match) - LLM had not chosen illustrated_scene for this topic")
        break
    return clips


def force_enclosure_primitive(clips: list, log=None) -> list:
    """
    Same pattern again, for the enclosure primitive: if the LLM picked
    concept_diagram (central_node + 3-6 leaf_nodes) for a topic that names a
    container/boundary structure (a cell, a parliament, a factory...),
    convert it to illustrated_scene + enclosure - reusing the LLM's own
    central_node as the boundary label and leaf_nodes as contents. Must run
    before force_branching_primitive for the same reason as
    force_paired_organ_primitive.
    """
    log = log or (lambda msg: None)
    if any(c.get("_curated_diagram_id") or c.get("_primitive_shape") for c in clips):
        return clips

    for idx, clip in enumerate(clips):
        if clip.get("template_id") != "concept_diagram":
            continue
        data = clip.get("template_data") or {}
        leaf_nodes = data.get("leaf_nodes") or []
        if not (3 <= len(leaf_nodes) <= 6):
            continue
        if not any(kw in _concept_diagram_donor_text(clip, data) for kw in ENCLOSURE_KEYWORDS):
            continue
        contents = [{"label": n.get("text", "")} for n in leaf_nodes if n.get("text")]
        if len(contents) < 3:
            continue
        central = data.get("central_node") or {}
        generated = generate_enclosure_diagram({
            "boundary": {"label": central.get("text", "")},
            "contents": contents,
        })
        if not generated:
            continue
        clip["template_id"] = "illustrated_scene"
        clip["beat_shape"] = "spatial"
        clip["template_data"] = {
            "title": data.get("title") or clip.get("purpose") or "",
            "animation_action": "rise",
            "elements": generated,
            "primitive_shape": "enclosure",
        }
        clip["_primitive_shape"] = "enclosure"
        log(f"   [AUDIT REPAIR] Scene {idx + 1} force-converted from concept_diagram to illustrated_scene + enclosure primitive (topic keyword match) - LLM had not chosen illustrated_scene for this topic")
        break
    return clips


def force_node_network_primitive(clips: list, log=None) -> list:
    """
    Same pattern again, for the node_network primitive: if the LLM picked
    concept_diagram (central_node + 2-6 leaf_nodes) for a topic that names a
    genuinely connected/wired system (a circuit, a food web, a trade
    network...), convert it to illustrated_scene + node_network - reusing
    the LLM's own central_node and leaf_nodes as the graph's nodes. Confirmed
    live: gpt-4o-mini reliably prefers concept_diagram/branching's
    hub-and-spoke framing for circuit questions even with a strengthened
    illustrated_scene prompt hint, so a topic-gated backstop (same fix
    pattern as paired_organ/enclosure) is needed here too, not just prompt
    wording.

    Unlike the star topology concept_diagram naturally implies (central_node
    connected to every leaf), a circuit/food-chain-shaped topic is a LOOP -
    current flows battery -> switch -> resistor -> bulb -> back to battery,
    not everything wired only to one hub - so this builds a closed ring
    connecting central_node and all leaf_nodes in sequence, which is a much
    closer structural match to what these topics actually are. Must run
    before force_branching_primitive for the same reason as
    force_paired_organ_primitive.
    """
    log = log or (lambda msg: None)
    if any(c.get("_curated_diagram_id") or c.get("_primitive_shape") for c in clips):
        return clips

    for idx, clip in enumerate(clips):
        if clip.get("template_id") != "concept_diagram":
            continue
        data = clip.get("template_data") or {}
        leaf_nodes = data.get("leaf_nodes") or []
        if not (2 <= len(leaf_nodes) <= 6):
            continue
        if not any(kw in _concept_diagram_donor_text(clip, data) for kw in NETWORK_KEYWORDS):
            continue
        central = data.get("central_node") or {}
        node_labels = [central.get("text", "")] + [n.get("text", "") for n in leaf_nodes if n.get("text")]
        node_labels = [lbl for lbl in node_labels if lbl]
        if len(node_labels) < 3:
            continue
        nodes = [{"label": lbl} for lbl in node_labels]
        connections = [[i, (i + 1) % len(nodes)] for i in range(len(nodes))]
        generated = generate_node_network_diagram({"nodes": nodes, "connections": connections})
        if not generated:
            continue
        clip["template_id"] = "illustrated_scene"
        clip["beat_shape"] = "process_spatial"
        clip["template_data"] = {
            "title": data.get("title") or clip.get("purpose") or "",
            "animation_action": "rise",
            "elements": generated,
            "primitive_shape": "node_network",
        }
        clip["_primitive_shape"] = "node_network"
        log(f"   [AUDIT REPAIR] Scene {idx + 1} force-converted from concept_diagram to illustrated_scene + node_network primitive (topic keyword match, closed-ring connections) - LLM had not chosen illustrated_scene for this topic")
        break
    return clips


def force_branching_primitive(clips: list, log=None) -> list:
    """
    Closes the same reliability gap as force_curated_diagram_scene, but for
    the branching primitive instead of a fixed curated diagram: if the LLM
    picked concept_diagram (central_node + 2-6 leaf_nodes) for a topic that
    is actually branching-shaped, convert it to illustrated_scene using the
    branching primitive - reusing the LLM's own central_node/leaf_nodes as
    source/branches. This needs no new LLM cooperation (unlike a from-scratch
    primitive, which requires real per-topic labels only the LLM can supply)
    because concept_diagram's hub-and-spoke data is already structurally
    identical to what the branching primitive needs. Only acts if no
    curated/primitive diagram already exists in the lesson, and only converts
    one scene (the first eligible one) to avoid over-converting. Deliberately
    has no topic keyword gate, so it must run AFTER force_paired_organ_primitive
    and force_enclosure_primitive - it's the generic fallback for any
    2-6-leaf concept_diagram that isn't a more specific shape.
    """
    log = log or (lambda msg: None)
    if any(c.get("_curated_diagram_id") or c.get("_primitive_shape") for c in clips):
        return clips  # a real diagram already exists somewhere in this lesson

    for idx, clip in enumerate(clips):
        if clip.get("template_id") != "concept_diagram":
            continue
        data = clip.get("template_data") or {}
        leaf_nodes = data.get("leaf_nodes") or []
        if not (2 <= len(leaf_nodes) <= 6):
            continue
        branches = [{"label": n.get("text", ""), "cluster": False} for n in leaf_nodes if n.get("text")]
        if len(branches) < 2:
            continue
        central = data.get("central_node") or {}
        generated = generate_branching_diagram({"source": {"label": central.get("text", "")}, "branches": branches})
        if not generated:
            continue
        clip["template_id"] = "illustrated_scene"
        clip["beat_shape"] = "process_spatial"
        clip["template_data"] = {
            "title": data.get("title") or clip.get("purpose") or "",
            "animation_action": "rise",
            "elements": generated,
            "primitive_shape": "branching",
        }
        clip["_primitive_shape"] = "branching"
        log(f"   [AUDIT REPAIR] Scene {idx + 1} force-converted from concept_diagram to illustrated_scene + branching primitive (reused its own {len(branches)} leaf_nodes as branches) - LLM had not chosen illustrated_scene for this topic")
        break
    return clips


def apply_primitive_diagrams(clips: list, log=None) -> list:
    """For illustrated_scene clips where the LLM supplied a `primitive_shape`
    field (instead of, or in addition to, freehand `elements`), replace
    template_data.elements with the procedurally-generated, guaranteed-correct
    layout. Runs independently of apply_curated_diagrams - a curated exact
    match still wins when one exists; this handles everything else that fits
    a known generic shape."""
    log = log or (lambda msg: None)
    for clip in clips:
        if clip.get("template_id") != "illustrated_scene":
            continue
        if clip.get("_curated_diagram_id"):
            continue  # an exact curated match already won - don't override it
        data = clip.get("template_data") or {}
        primitive = data.get("primitive_shape")
        if not primitive or primitive not in DIAGRAM_PRIMITIVES:
            continue
        params = data.get("primitive_params") or {}
        try:
            generated = DIAGRAM_PRIMITIVES[primitive](params)
        except Exception as e:
            logger.error(f"[TemplateRegistry] primitive_shape='{primitive}' generation failed: {e}")
            continue
        if generated:
            data["elements"] = generated
            clip["template_data"] = data
            clip["_primitive_shape"] = primitive
            log(f"   [AUDIT REPAIR] illustrated_scene used primitive_shape='{primitive}' - generated {len(generated)} elements procedurally")
    return clips

_MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_MAIN_DIR, "..", "..", "..", ".."))
REGISTRY_PATH = os.path.join(_PROJECT_ROOT, "hyperframes_engine", "shared", "template-registry.json")
ICONS_PATH = os.path.join(_PROJECT_ROOT, "hyperframes_engine", "shared", "icons.js")
DIAGRAM_LIBRARY_PATH = os.path.join(_PROJECT_ROOT, "hyperframes_engine", "shared", "diagram-library.json")

_cache = None
_icon_names_cache = None
_icon_categories_cache = None
_diagram_library_cache = None


def load_diagram_library() -> dict:
    """{diagram_id: {keywords, elements}} - curated illustrated_scene layouts.
    See diagram-library.json's own header comment for why this exists."""
    global _diagram_library_cache
    if _diagram_library_cache is not None:
        return _diagram_library_cache
    try:
        with open(DIAGRAM_LIBRARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _diagram_library_cache = data.get("diagrams", {})
    except Exception as e:
        logger.error(f"[TemplateRegistry] Failed to load diagram library from {DIAGRAM_LIBRARY_PATH}: {e}")
        _diagram_library_cache = {}
    return _diagram_library_cache


def match_diagram_with_score(*text_fields):
    """(diagram_id, score) for the best-matching curated diagram against the
    given scene text, or (None, 0) if the library is empty/nothing matches."""
    library = load_diagram_library()
    if not library:
        return None, 0
    haystack = " ".join(str(t) for t in text_fields if t).lower()
    if not haystack:
        return None, 0
    best_id, best_score = None, 0
    for diagram_id, meta in library.items():
        score = sum(1 for kw in meta.get("keywords", []) if kw.lower() in haystack)
        if score > best_score:
            best_id, best_score = diagram_id, score
    return best_id, best_score


def match_diagram_for_scene(*text_fields) -> str:
    """Best-matching diagram_id from the curated library for the given scene
    text (purpose/title/teacher_script), by keyword overlap - a simple,
    deterministic retrieval step, not an LLM call. Returns None if nothing
    matches meaningfully (caller should keep the LLM's own freeform elements
    in that case - the curated library is a targeted override, not a
    replacement for illustrated_scene as a whole)."""
    diagram_id, _ = match_diagram_with_score(*text_fields)
    return diagram_id


# Minimum keyword-overlap score required before we FORCE a scene that the LLM
# chose a different (non-illustrated_scene) template for into illustrated_scene
# - a higher bar than the in-place override in apply_curated_diagrams, since
# this is a bigger intervention (changing which template renders the scene at
# all, not just what an already-chosen illustrated_scene draws).
FORCE_MATCH_MIN_SCORE = 2


def force_curated_diagram_scene(clips: list, log=None) -> list:
    """
    If the whole lesson matches a curated diagram topic strongly but the LLM
    never actually chose illustrated_scene for it (it picked concept_diagram /
    cycle_template / etc. instead - a real, observed case: "digestive system"
    rendered as a concept map + cycle instead of the verified anatomy layout),
    convert the single best-matching scene to illustrated_scene with the
    curated diagram. Only acts when no scene already got a curated match via
    apply_curated_diagrams (never overrides a choice the LLM already got
    right), and only above FORCE_MATCH_MIN_SCORE to avoid forcing a diagram
    onto a scene that only loosely mentions the topic in passing.
    """
    log = log or (lambda msg: None)
    if any(c.get("_curated_diagram_id") for c in clips):
        return clips  # a scene already legitimately uses a curated diagram
    # Same rule as apply_curated_diagrams: an explicit primitive_shape is a
    # real structured signal, not something a fuzzy keyword guess should
    # override (e.g. a heart/blood_flow scene that mentions "lungs" in
    # passing shouldn't get bumped to the full respiratory diagram).
    if any((c.get("template_data") or {}).get("primitive_shape") in DIAGRAM_PRIMITIVES for c in clips):
        return clips

    best_idx, best_id, best_score = None, None, 0
    for idx, clip in enumerate(clips):
        if clip.get("template_id") in ("title_slide", "quiz_checkpoint"):
            continue
        data = clip.get("template_data") or {}
        diagram_id, score = match_diagram_with_score(clip.get("purpose"), data.get("title"), clip.get("teacher_script"))
        if diagram_id and score > best_score:
            best_idx, best_id, best_score = idx, diagram_id, score

    if best_idx is None or best_score < FORCE_MATCH_MIN_SCORE:
        return clips

    library = load_diagram_library()
    clip = clips[best_idx]
    original_title = (clip.get("template_data") or {}).get("title") or clip.get("purpose") or best_id.replace("_", " ").title()
    clip["template_id"] = "illustrated_scene"
    clip["beat_shape"] = "process_spatial"
    clip["template_data"] = {"title": original_title, "animation_action": "rise", "elements": library[best_id]["elements"]}
    clip["_curated_diagram_id"] = best_id
    log(f"   [AUDIT REPAIR] Scene {best_idx + 1} force-converted to illustrated_scene + curated diagram '{best_id}' (score={best_score}) - LLM had chosen a different template for this topic")
    return clips


def apply_curated_diagrams(clips: list, log=None) -> list:
    """For every illustrated_scene clip whose purpose/title/teacher_script
    matches a curated diagram closely enough, replace its LLM-authored
    template_data.elements with the curated, hand-verified layout in place -
    keeping the scene's own title/animation_action. Mutates and returns
    `clips`. This is what stops 'draw a respiratory system' from becoming
    generic unlabelled circles - the LLM's freehand geometry for real-world
    recognizable subjects (anatomy, natural processes) is unreliable, so a
    known match always wins over what the LLM invented."""
    log = log or (lambda msg: None)
    library = load_diagram_library()
    if not library:
        return clips
    for clip in clips:
        if clip.get("template_id") != "illustrated_scene":
            continue
        data = clip.get("template_data") or {}
        title = data.get("title", "")
        matched_id, score = match_diagram_with_score(clip.get("purpose"), title, clip.get("teacher_script"))
        # An explicit primitive_shape is normally a real structured signal of
        # what the LLM meant (e.g. container_flow for "blood through the
        # heart") that a fuzzy keyword guess shouldn't override - guards
        # against an incidental word (a heart scene mentioning "lungs" in
        # passing) false-positiving into the wrong curated diagram. But a
        # STRONG match (>= FORCE_MATCH_MIN_SCORE) is real evidence the scene
        # actually IS that curated topic, not an incidental mention - and a
        # hand-verified anatomy diagram beats a generic primitive shape for
        # its own topic every time. Confirmed live: a real "Stomach" scene
        # scored 2 (title="Stomach" + purpose mentions "digestion") but the
        # LLM had picked primitive_shape=container_flow (a bare rectangle)
        # for it - the unconditional skip below meant the curated, richly-
        # detailed digestive_system diagram never got a chance even though
        # this is exactly the topic it exists for.
        if data.get("primitive_shape") in DIAGRAM_PRIMITIVES and score < FORCE_MATCH_MIN_SCORE:
            continue
        if matched_id:
            curated = library[matched_id]
            data["elements"] = curated["elements"]
            clip["template_data"] = data
            clip["_curated_diagram_id"] = matched_id
            log(f"   [AUDIT REPAIR] illustrated_scene matched curated diagram '{matched_id}' - replaced LLM-authored elements with verified layout")
    return clips

# The app only has 4 real subjects (see backend/app/core/subject_config.py) -
# each is a combined subject covering several of the icon categories tagged
# in hyperframes_engine/shared/icons.js's HFIconCategories. 'general' is
# always included regardless of subject.
SUBJECT_TO_ICON_CATEGORIES = {
    "science": ["physics", "chemistry", "biology"],
    "social": ["history", "geography", "civics"],
    "maths": ["math"],
    "english": [],
}


def get_icon_names() -> list:
    """
    Names of the curated icons available to the LLM (see
    hyperframes_engine/shared/icons.js). Extracted from the JS source by
    regex instead of duplicating the list here, so the two stay in sync
    automatically when an icon is added/removed.
    """
    global _icon_names_cache
    if _icon_names_cache is not None:
        return _icon_names_cache
    import re
    try:
        with open(ICONS_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        names = re.findall(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*):\s*'<", text, re.MULTILINE)
        _icon_names_cache = [n for n in names if n != "dot"]
    except Exception as e:
        logger.error(f"[TemplateRegistry] Failed to load icon names from {ICONS_PATH}: {e}")
        _icon_names_cache = []
    return _icon_names_cache


def get_icon_categories() -> dict:
    """{category: [icon_names]} parsed from HFIconCategories in icons.js -
    used to build a per-subject icon shortlist (retrieval) instead of dumping
    the full ~216-icon library into every prompt (recall)."""
    global _icon_categories_cache
    if _icon_categories_cache is not None:
        return _icon_categories_cache
    import re
    categories = {}
    try:
        with open(ICONS_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        block_match = re.search(r"var HFIconCategories = \{([\s\S]*?)\n\};", text)
        if block_match:
            block = block_match.group(1)
            for cat_match in re.finditer(r"(\w+):\s*\[([^\]]*)\]", block):
                cat_name = cat_match.group(1)
                items = re.findall(r"'([a-zA-Z_][a-zA-Z0-9_]*)'", cat_match.group(2))
                categories[cat_name] = items
    except Exception as e:
        logger.error(f"[TemplateRegistry] Failed to load icon categories from {ICONS_PATH}: {e}")
    _icon_categories_cache = categories
    return _icon_categories_cache


def get_icon_names_for_subject(subject: str = None) -> list:
    """Subject-filtered icon shortlist: relevant categories + 'general', falling
    back to the full list if the subject isn't recognized or categories failed
    to load. This is the retrieval step - the LLM only ever sees icons plausibly
    relevant to the subject it's writing about, not the entire library."""
    all_names = get_icon_names()
    if not subject:
        return all_names
    categories = get_icon_categories()
    if not categories:
        return all_names
    subject_key = str(subject).strip().lower()
    wanted_cats = SUBJECT_TO_ICON_CATEGORIES.get(subject_key)
    if wanted_cats is None:
        return all_names
    shortlist = set(categories.get("general", []))
    for cat in wanted_cats:
        shortlist.update(categories.get(cat, []))
    filtered = [n for n in all_names if n in shortlist]
    return filtered if filtered else all_names


def load_registry() -> dict:
    """Returns the {template_id: {...}} mapping, cached after first read."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache = data.get("templates", {})
    except Exception as e:
        logger.error(f"[TemplateRegistry] Failed to load {REGISTRY_PATH}: {e}")
        _cache = {}
    return _cache


def get_active_template_ids(extra_ids: list = None) -> list:
    """Template ids the LLM is allowed to choose from (status == 'active').

    `extra_ids`: template ids to include regardless of their registry
    `status` (e.g. a banned-but-code-complete template like 'image_scene'
    being exercised by an isolated test harness). None (the default, every
    production call) preserves today's exact behavior - this parameter only
    exists so a test-only caller can widen the choice set for its own
    prompt/repair-pass calls without editing template-registry.json's
    `status` field, which would affect production immediately since the
    registry is the single source of truth for both."""
    registry = load_registry()
    ids = [tid for tid, meta in registry.items() if meta.get("status") == "active"]
    for tid in (extra_ids or []):
        if tid in registry and tid not in ids:
            ids.append(tid)
    return ids


def get_constraint_map(extra_ids: list = None) -> dict:
    """{template_id: [constraint_strings]} for active templates (+ extra_ids)."""
    registry = load_registry()
    valid = get_active_template_ids(extra_ids)
    return {tid: registry[tid].get("constraints", []) for tid in valid if tid in registry}


# Canonical information-shape vocabulary the LLM is asked to classify each
# scene as, alongside its template_id, in the same generation call (no extra
# LLM round-trip). This is what lets repair_scene_templates() catch a
# structurally-legal but semantically-wrong pick, e.g. a comparison scene
# that picked cycle_template - something the old constraint-only repair pass
# could never see, since a shape mismatch isn't a placement/uniqueness rule.
SHAPE_VOCAB = [
    "sequence", "hierarchy", "comparison", "cause_effect",
    "process_spatial", "quantitative", "cyclical", "spatial", "overlap",
]


def get_shape_map(extra_ids: list = None) -> dict:
    """{template_id: [shape_strings]} for active templates (+ extra_ids)."""
    registry = load_registry()
    valid = get_active_template_ids(extra_ids)
    return {tid: registry[tid].get("shapes", []) for tid in valid if tid in registry}


def build_shape_guidance_text(extra_ids: list = None) -> str:
    """
    Instructs the LLM to classify each scene's underlying information-shape
    BEFORE choosing a template_id, and shows which templates fit which shape
    - forcing the "what kind of idea is this" reasoning step to happen
    in-line rather than being skipped straight to a template pick.
    """
    shape_map = get_shape_map(extra_ids)
    by_shape = {}
    for tid, shapes in shape_map.items():
        for s in shapes:
            by_shape.setdefault(s, []).append(tid)

    lines = ["### SCENE SHAPE (classify BEFORE picking a template):",
             "For every scene, first decide what kind of idea it is conveying - this becomes the `beat_shape` field - THEN pick the template_id that actually matches that shape:"]
    for shape in SHAPE_VOCAB:
        tids = by_shape.get(shape, [])
        if not tids:
            continue
        tid_list = ", ".join(f"'{t}'" for t in tids)
        lines.append(f"- **{shape}**: fits {tid_list}")
    lines.append(
        "A scene's `template_id` MUST be one whose shape matches its own `beat_shape` "
        "(e.g. a `comparison` beat must use `column_comparison` or `venn_diagram`, never `cycle_template`). "
        "Mismatches will be auto-corrected, discarding your narration reasoning for that scene, so get the shape right the first time."
    )
    return "\n".join(lines)


def build_template_choice_line(extra_ids: list = None) -> str:
    """e.g. "'title_slide', 'concept_diagram', ..." for embedding in the prompt."""
    return ", ".join(f"'{tid}'" for tid in get_active_template_ids(extra_ids))


def build_icon_guidance_text(subject: str = None) -> str:
    """
    Instructs the LLM to attach an 'icon' name (from the curated icon set) to
    every labeled item it can - center concepts, leaf nodes, cycle stages,
    taxonomy branches, comparison bullets, venn items, before/after bullets,
    and map markers. This is what turns those templates from plain text-in-
    boxes into something actually visual: each icon is a real rendered SVG
    picked from hyperframes_engine/shared/icons.js, not LLM-generated art, so
    an unrecognized/omitted icon name always degrades gracefully to a plain
    dot rather than breaking the scene.

    When `subject` is given (science/social/maths/english), the icon list is
    filtered to that subject's relevant categories + general icons (retrieval)
    instead of showing the full ~216-icon library on every request (recall) -
    a shorter, more relevant list is both cheaper and easier for the LLM to
    pick well from.
    """
    icon_names = get_icon_names_for_subject(subject)
    if not icon_names:
        return ""
    icon_list = ", ".join(f"'{n}'" for n in icon_names)
    return (
        "### VISUAL ICONS (make every scene actually visual, not just text):\n"
        "For concept_diagram, cycle_template, taxonomy_tree, column_comparison, "
        "venn_diagram, before_after_slider, and geo_marker: give EVERY labeled item "
        "(the central concept, each leaf/branch/stage/bullet/marker) an `\"icon\"` "
        "field alongside its text, e.g. {\"text\": \"Evaporation\", \"icon\": \"sun\"}. "
        f"Choose the closest matching icon name from this list (already narrowed to icons relevant to this subject): {icon_list}. "
        "Pick the most semantically relevant icon for each concept (e.g. 'sun' for "
        "heat/energy/day, 'water_drop' for liquids, 'leaf' for plants, 'brain' for "
        "thinking/biology, 'factory' for industry, 'book' for learning). Never invent "
        "an icon name outside this list - if nothing fits well, omit the icon field "
        "for that item rather than guessing."
    )


def build_template_data_hints_block(indent: str = "        ", extra_ids: list = None) -> str:
    """Multi-line '// For <id>: <hint>' comment block for the prompt's template_data example."""
    registry = load_registry()
    lines = []
    for tid in get_active_template_ids(extra_ids):
        hint = registry[tid].get("template_data_hint", "")
        lines.append(f"{indent}// For '{tid}': {hint}")
    return "\n".join(lines)


def build_selection_rules_text(extra_ids: list = None) -> str:
    """Numbered prose rules derived from each active template's best_for + constraints."""
    registry = load_registry()
    lines = []
    n = 1
    for tid in get_active_template_ids(extra_ids):
        meta = registry[tid]
        rule = f"{n}. **{tid}**: {meta.get('best_for', '')}."
        constraints = meta.get("constraints", [])
        if "scene_1_only" in constraints:
            rule += " MUST be used ONLY for Scene 1."
        for c in constraints:
            if c.startswith("max_uses:"):
                rule += f" Use at most {c.split(':', 1)[1]} time(s) per lesson."
            if c == "last_scene_only":
                rule += " Use ONLY as the final scene."
        lines.append(rule)
        n += 1

    # A template listed in extra_ids is deliberately being offered despite its
    # registry status (e.g. a test harness exercising a banned-but-code-complete
    # template) - it must not also appear in its own "do not use" list below.
    banned = [tid for tid, meta in registry.items() if meta.get("status") == "banned" and tid not in (extra_ids or [])]
    if banned:
        banned_list = ", ".join(f"`{tid}`" for tid in banned)
        lines.append(f"{n}. **Do NOT use** these template ids under any circumstances: {banned_list}.")
    return "\n".join(lines)


def _find_scene_1_template(valid: list, constraints: dict) -> str:
    for tid in valid:
        if "scene_1_only" in constraints.get(tid, []):
            return tid
    return valid[0] if valid else "concept_diagram"


def _pick_replacement(valid: list, constraints: dict, avoid: set, use_counts: dict,
                       shape_map: dict = None, required_shape: str = None) -> str:
    """First valid, non-scene-1-only, non-last-scene-only template not in `avoid`
    and not already at its max_uses limit. When `required_shape` is given, prefers
    a template whose registry `shapes` include it (falls through to any fitting
    template if none match, rather than returning nothing)."""
    def _candidates(require_shape_match: bool):
        for tid in valid:
            if tid in avoid:
                continue
            tid_constraints = constraints.get(tid, [])
            if "scene_1_only" in tid_constraints or "last_scene_only" in tid_constraints:
                continue
            if require_shape_match and required_shape and required_shape not in (shape_map or {}).get(tid, []):
                continue
            max_uses = None
            for c in tid_constraints:
                if c.startswith("max_uses:"):
                    max_uses = int(c.split(":", 1)[1])
            if max_uses is not None and use_counts.get(tid, 0) >= max_uses:
                continue
            yield tid

    if required_shape:
        for tid in _candidates(require_shape_match=True):
            return tid
    for tid in _candidates(require_shape_match=False):
        return tid
    # Nothing else fits - fall back to the first valid template even if imperfect,
    # rather than leaving an invalid/banned template_id in place.
    return valid[0] if valid else "concept_diagram"


def _extract_ordered_items(data: dict) -> list:
    """Best-effort extraction of an ordered [{label, description}] list from
    whatever sequence-shaped field happens to be populated (events/stages/
    steps/leaf_nodes) - used by _adapt_template_data_for_swap below to reuse
    a scene's real content across a template_id swap within the sequence
    family, instead of losing it."""
    for key in ("events", "stages", "steps", "leaf_nodes"):
        raw = data.get(key)
        if not raw:
            continue
        items = []
        for it in raw:
            if isinstance(it, str):
                items.append({"label": it, "description": ""})
            elif isinstance(it, dict):
                label = it.get("label") or it.get("text") or it.get("name") or ""
                desc = it.get("description") or it.get("value") or it.get("result") or ""
                if label:
                    items.append({"label": str(label), "description": str(desc)})
        if items:
            return items

    # column_comparison shape: {left_col: {header, bullets: [{text}]}, right_col: {...}}
    # Confirmed live gap (2026-08-26): a column_comparison scene force-swapped
    # to math_derivation on a beat_shape mismatch kept its left_col/right_col
    # data untouched (not recognized by any case above), so math_derivation
    # got no formula/steps to render at all - a genuinely blank scene, not
    # just an imperfect one. Each column's header becomes its own item
    # (a section label), followed by its bullets in order.
    left_col, right_col = data.get("left_col"), data.get("right_col")
    if isinstance(left_col, dict) or isinstance(right_col, dict):
        items = []
        for col in (left_col, right_col):
            if not isinstance(col, dict):
                continue
            header = col.get("header")
            if header:
                items.append({"label": str(header), "description": ""})
            for b in (col.get("bullets") or []):
                text = b.get("text", "") if isinstance(b, dict) else (b if isinstance(b, str) else "")
                if text:
                    items.append({"label": str(text), "description": ""})
        if items:
            return items

    return []


def _adapt_template_data_for_swap(new_tid: str, data: dict) -> dict:
    """repair_scene_templates() below swaps a scene's template_id in several
    places (shape mismatch, misplaced/invalid, consecutive duplicate,
    max_uses) but historically left template_data untouched - so a scene
    swapped e.g. from horizontal_timeline to math_derivation kept `events`
    instead of `formula`/`steps`, rendering with a title and nothing else
    (confirmed live: a maths lesson's summary scene silently lost all its
    step content this way). Only the three sequence-family templates
    (math_derivation/cycle_template/horizontal_timeline) are safe to
    auto-adapt between, since their content is really just an ordered list
    of steps/stages/events under different key names - anything else
    (concept_diagram, illustrated_scene, ...) needs real per-shape data only
    the LLM can supply, so this deliberately does nothing for those."""
    if new_tid not in ("math_derivation", "cycle_template", "horizontal_timeline"):
        return data
    has_native_shape = {
        "math_derivation": bool(data.get("formula") or data.get("steps") or data.get("equation_steps")),
        "cycle_template": bool(data.get("stages")),
        "horizontal_timeline": bool(data.get("events")),
    }[new_tid]
    if has_native_shape:
        return data
    items = _extract_ordered_items(data)
    if not items:
        return data
    data = dict(data)
    if new_tid == "math_derivation":
        data["steps"] = [f"{it['label']}: {it['description']}" if it["description"] else it["label"] for it in items]
    elif new_tid == "cycle_template":
        data["stages"] = [{"label": it["label"]} for it in items]
    elif new_tid == "horizontal_timeline":
        data["events"] = [{"label": it["label"], "description": it["description"]} for it in items]
    return data


def _template_data_matches_native_shape(tid: str, data: dict) -> bool:
    """True if `data` already looks like real, correctly-shaped content for
    `tid` specifically - used by the beat_shape mismatch check below to tell
    a bad template CHOICE (swap warranted, e.g. a comparison beat that
    picked cycle_template with no comparison-shaped data) apart from a bad
    beat_shape LABEL on an otherwise-correct, content-rich scene (confirmed
    live: the LLM produced a real math_derivation scene with a formula and
    steps, but self-labeled it 'process_spatial' instead of 'quantitative' -
    swapping it to illustrated_scene per the label would have discarded
    real content for an empty scene with none)."""
    if tid == "math_derivation":
        return bool(data.get("formula") or data.get("steps") or data.get("equation_steps"))
    if tid == "cycle_template":
        return bool(data.get("stages"))
    if tid == "horizontal_timeline":
        return bool(data.get("events"))
    if tid == "illustrated_scene":
        return bool(data.get("elements") or data.get("primitive_shape"))
    if tid == "concept_diagram":
        return bool(data.get("central_node") and data.get("leaf_nodes"))
    return False


_SHAPE_CHECKABLE_TEMPLATES = ("math_derivation", "cycle_template", "horizontal_timeline", "illustrated_scene", "concept_diagram")


def _infer_native_template_id(data: dict):
    """Which of the shape-checkable templates `data`'s actual fields belong
    to, if any and only one does."""
    for tid in _SHAPE_CHECKABLE_TEMPLATES:
        if _template_data_matches_native_shape(tid, data):
            return tid
    return None


def _fix_template_id_data_mismatch(clip: dict, valid: list, shape_map: dict, log) -> None:
    """Corrects cases where template_id and template_data disagree about what
    template a scene actually is - independent of beat_shape, which is only
    the LLM's self-declared label and isn't always trustworthy (confirmed
    live: a real scene had template_id='cycle_template' with template_data
    shaped as illustrated_scene's primitive_shape/primitive_params, no
    'stages' anywhere - Renderer.renderCycleTemplate built an empty stages
    container, rendering as a blank scene with just a title and orbit dot).
    template_data's actual field names are structural ground truth in a way
    beat_shape isn't, so this runs before any beat_shape-based check and
    corrects template_id (and beat_shape, to stay consistent) to match the
    data's real shape whenever the two disagree unambiguously."""
    tid = clip.get("template_id")
    if tid not in _SHAPE_CHECKABLE_TEMPLATES or tid not in valid:
        return
    data = clip.get("template_data") or {}
    if _template_data_matches_native_shape(tid, data):
        return
    inferred = _infer_native_template_id(data)
    if not inferred or inferred == tid or inferred not in valid:
        return
    log(f"   [AUDIT REPAIR] template_id='{tid}' didn't match its own template_data's shape (no matching fields) - template_data is actually shaped for '{inferred}', corrected template_id to match")
    clip["template_id"] = inferred
    new_shapes = shape_map.get(inferred, [])
    if new_shapes and clip.get("beat_shape") not in new_shapes:
        clip["beat_shape"] = new_shapes[0]


def repair_scene_templates(clips: list, log=None, extra_ids: list = None) -> list:
    """
    Enforces registry-derived constraints on an LLM-produced scene list in place:
    scene-1-only / last-scene-only placement, max_uses caps, and no consecutive
    duplicate template_ids. Mutates and returns `clips` (each a dict with a
    'template_id' key). `log` is an optional callable(str) for audit output.
    `extra_ids` widens the valid set the same way get_active_template_ids()
    does - without it, a scene using a banned-but-offered template (e.g. a
    test harness's 'image_scene') would be treated as invalid and swapped
    away by this same repair pass.
    """
    log = log or (lambda msg: None)
    valid = get_active_template_ids(extra_ids)
    constraints = get_constraint_map(extra_ids)
    shape_map = get_shape_map(extra_ids)
    use_counts = {}
    last_idx = len(clips) - 1
    shape_mismatch_count = 0

    for idx, clip in enumerate(clips):
        if idx > 0:
            _fix_template_id_data_mismatch(clip, valid, shape_map, log)

        tid = clip.get("template_id")
        tid_constraints = constraints.get(tid, None)

        if idx == 0:
            forced = _find_scene_1_template(valid, constraints)
            if tid != forced:
                clip["template_id"] = forced
                log(f"   [AUDIT REPAIR] Scene 1 forced to '{forced}'")
            tid = clip["template_id"]
        else:
            invalid = tid_constraints is None
            misplaced = tid_constraints is not None and (
                "scene_1_only" in tid_constraints
                or ("last_scene_only" in tid_constraints and idx != last_idx)
            )
            if invalid or misplaced:
                avoid = {clips[idx - 1].get("template_id")}
                replacement = _pick_replacement(valid, constraints, avoid, use_counts)
                log(f"   [AUDIT REPAIR] Swapped disallowed/misplaced template '{tid}' in Scene {idx + 1} to '{replacement}'")
                clip["template_id"] = replacement
                clip["template_data"] = _adapt_template_data_for_swap(replacement, clip.get("template_data") or {})
                tid = replacement

            if tid == clips[idx - 1].get("template_id"):
                avoid = {tid}
                replacement = _pick_replacement(valid, constraints, avoid, use_counts)
                log(f"   [AUDIT REPAIR] Swapped consecutive duplicate '{tid}' in Scene {idx + 1} to '{replacement}'")
                clip["template_id"] = replacement
                clip["template_data"] = _adapt_template_data_for_swap(replacement, clip.get("template_data") or {})
                tid = replacement

            # Semantic check (not just structural): does the LLM's own declared
            # beat_shape for this scene actually match the shapes the chosen
            # template is registered for? Catches a legally-placed, non-duplicate
            # template that is still the wrong SHAPE for its content - e.g. a
            # comparison beat that picked cycle_template - which the checks
            # above can never see since they only look at placement/uniqueness.
            # shape_map.get(tid) must be non-empty before this check means anything -
            # a template with NO declared shapes (e.g. 'image_scene', which never
            # needed a 'shapes' entry while banned) would otherwise match zero
            # shapes by definition and get swapped away regardless of its actual
            # beat_shape. Every currently-active template does declare shapes, so
            # this only ever changes behavior for a shapeless template - never for
            # today's production template set.
            beat_shape = clip.get("beat_shape")
            if beat_shape and beat_shape in SHAPE_VOCAB and shape_map.get(tid) and beat_shape not in shape_map.get(tid, []):
                if _template_data_matches_native_shape(tid, clip.get("template_data") or {}):
                    # The scene's own content is unambiguous evidence of tid's
                    # real shape - the LLM mislabeled beat_shape, not the
                    # template. Correct the label instead of discarding real
                    # content via a swap into a template with no matching data.
                    real_shapes = shape_map.get(tid, [])
                    if real_shapes:
                        log(f"   [AUDIT REPAIR] Scene {idx + 1} beat_shape='{beat_shape}' contradicted its own content (already valid for '{tid}') - corrected beat_shape to '{real_shapes[0]}' instead of swapping away real content")
                        clip["beat_shape"] = real_shapes[0]
                else:
                    avoid = {clips[idx - 1].get("template_id")}
                    replacement = _pick_replacement(valid, constraints, avoid, use_counts, shape_map=shape_map, required_shape=beat_shape)
                    if replacement != tid:
                        shape_mismatch_count += 1
                        log(f"   [AUDIT REPAIR] Shape mismatch: scene {idx + 1} beat_shape='{beat_shape}' doesn't fit '{tid}' (shapes={shape_map.get(tid, [])}) - swapped to '{replacement}'")
                        clip["template_id"] = replacement
                        clip["template_data"] = _adapt_template_data_for_swap(replacement, clip.get("template_data") or {})
                        tid = replacement

        # Enforce max_uses after any swap above may have changed tid
        tid_constraints = constraints.get(tid, [])
        max_uses = None
        for c in tid_constraints:
            if c.startswith("max_uses:"):
                max_uses = int(c.split(":", 1)[1])
        if max_uses is not None and use_counts.get(tid, 0) >= max_uses:
            avoid = {tid, clips[idx - 1].get("template_id")} if idx > 0 else {tid}
            replacement = _pick_replacement(valid, constraints, avoid, use_counts)
            log(f"   [AUDIT REPAIR] '{tid}' exceeded max_uses in Scene {idx + 1}, swapped to '{replacement}'")
            clip["template_id"] = replacement
            clip["template_data"] = _adapt_template_data_for_swap(replacement, clip.get("template_data") or {})
            tid = replacement

        use_counts[tid] = use_counts.get(tid, 0) + 1

    logger.info(f"[HYPERFRAMES_TEMPLATE_DISTRIBUTION] {dict(use_counts)} shape_mismatches_corrected={shape_mismatch_count}")
    return clips
