#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chill Pet - a desktop pet drawn entirely in code (no image files).

Run:   python desktop_pet.py            (installs PySide6 automatically the first time)
Options: --scale 0.7   --mode follow|chill|still   --no-sleep
         --think / --no-think   (optional local LLM reaction layer, also in the menu: "Let him think")
         --llm-debug            (print generation latency + the longest frame stall while thinking)
         --model PATH           (use your own GGUF instead of the preferred local Qwen3-0.6B)

Controls
  Left-click ........ poke him        Double-click ... spin
  Drag .............. pick him up       Right-click .... menu (size, behavior, nap, quit)
  Tray icon ......... same menu (so you can always find the Quit button)

Windows only: he sits on the title bar of your windows (and rides along when you
drag them), vibes to music, watches videos, stays quiet while you code, and hides
during fullscreen apps/games. He only reads window rectangles + titles, locally.
"""
import math

# The optional animation pack lives beside this file.  It is pure data + math
# (no Qt, no imports from the pet) and only layers offsets onto a pose dict, so
# the pet keeps breathing, gazing and blinking underneath every clip.
try:
    import pet_anims
except Exception:                 # missing/corrupt pack must never stop the pet
    pet_anims = None

# ----------------------------------------------------------------- colours
INK   = (12, 12, 14, 255)
PAPER = (255, 255, 255, 255)
HALO  = (255, 255, 255, 235)
SOFT  = (12, 12, 14, 150)

# ----------------------------------------------------------------- matrices
def _chain(*ms):
    r = ms[0]
    for m in ms[1:]:
        r = m_mul(r, m)
    return r

def m_mul(m, n):
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1*a2 + c1*b2, b1*a2 + d1*b2,
            a1*c2 + c1*d2, b1*c2 + d1*d2,
            a1*e2 + c1*f2 + e1, b1*e2 + d1*f2 + f1)

def m_tr(tx, ty):
    return (1.0, 0.0, 0.0, 1.0, tx, ty)

def m_sc(sx, sy=None, px=0.0, py=0.0):
    if sy is None:
        sy = sx
    return (sx, 0.0, 0.0, sy, px - sx*px, py - sy*py)

def m_rot(deg, px=0.0, py=0.0):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (c, s, -s, c, px - (c*px - s*py), py - (s*px + c*py))

def m_pt(m, x, y):
    return (m[0]*x + m[2]*y + m[4], m[1]*x + m[3]*y + m[5])

def tpath(path, m):
    a, b, c, d, e, f = m
    out = []
    for cmd in path:
        k = cmd[0]
        if k == 'Z':
            out.append(cmd)
            continue
        v = cmd[1:]
        nv = []
        for i in range(0, len(v), 2):
            x, y = v[i], v[i+1]
            nv.append(a*x + c*y + e)
            nv.append(b*x + d*y + f)
        out.append((k,) + tuple(nv))
    return out

def mirror(path, axis=179.0):
    out = []
    for cmd in path:
        if cmd[0] == 'Z':
            out.append(cmd)
            continue
        v = cmd[1:]
        nv = []
        for i in range(0, len(v), 2):
            nv.append(2*axis - v[i])
            nv.append(v[i+1])
        out.append((cmd[0],) + tuple(nv))
    return out

# ----------------------------------------------------------------- path helpers
def smooth(pts, closed=False, t=1.0, start=True):
    n = len(pts)
    cmds = [('M', pts[0][0], pts[0][1])] if start else []
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p1 = pts[i]
        p2 = pts[(i+1) % n]
        p0 = pts[(i-1) % n] if (closed or i > 0) else p1
        p3 = pts[(i+2) % n] if (closed or i + 2 < n) else p2
        c1 = (p1[0] + (p2[0]-p0[0])*t/6.0, p1[1] + (p2[1]-p0[1])*t/6.0)
        c2 = (p2[0] - (p3[0]-p1[0])*t/6.0, p2[1] - (p3[1]-p1[1])*t/6.0)
        cmds.append(('C', c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]))
    if closed:
        cmds.append(('Z',))
    return cmds

def poly(pts, closed=True):
    cmds = [('M', pts[0][0], pts[0][1])]
    for p in pts[1:]:
        cmds.append(('L', p[0], p[1]))
    if closed:
        cmds.append(('Z',))
    return cmds

K = 0.5522847498
def ellipse(cx, cy, rx, ry, rot=0.0):
    r = math.radians(rot)
    c, s = math.cos(r), math.sin(r)
    m = (c*rx, s*rx, -s*ry, c*ry, cx, cy)
    u = [('M', 1, 0),
         ('C', 1, K, K, 1, 0, 1),
         ('C', -K, 1, -1, K, -1, 0),
         ('C', -1, -K, -K, -1, 0, -1),
         ('C', K, -1, 1, -K, 1, 0),
         ('Z',)]
    return tpath(u, m)

def lines(segs):
    """list of ((x0,y0),(x1,y1)) -> M/L path"""
    out = []
    for (a, b) in segs:
        out.append(('M', a[0], a[1]))
        out.append(('L', b[0], b[1]))
    return out

def spline_points(pts, per=8):
    """sample a catmull-rom spline through pts (open)"""
    segs = smooth(pts)
    out = [pts[0]]
    cur = pts[0]
    for cmd in segs[1:]:
        _, x1, y1, x2, y2, x3, y3 = cmd
        for i in range(1, per + 1):
            t = i / per
            u = 1 - t
            x = u**3*cur[0] + 3*u*u*t*x1 + 3*u*t*t*x2 + t**3*x3
            y = u**3*cur[1] + 3*u*u*t*y1 + 3*u*t*t*y2 + t**3*y3
            out.append((x, y))
        cur = (x3, y3)
    return out

# ----------------------------------------------------------------- static design
CX, CY = 179.0, 150.0          # pivot / centre of the drawing
NECK = (179.0, 96.0)
EAR_PIV_L = (104.0, 56.0)
EAR_PIV_R = (254.0, 56.0)
TAIL_BASE = (166.0, 253.0)
EYE_L = (132.0, 40.0, -28.0)    # cx, cy, tilt
EYE_R = (230.0, 38.5, 28.0)
EYE_RX, EYE_RY = 23.5, 15.6

def _build():
    G = {}
    # --- head outline
    run_a = [(155, 86), (159, 93), (166, 98), (173, 96.5), (179, 90)]
    run_b = [(179, 90), (185, 96.5), (192, 98), (199, 93), (202, 86)]
    run_c = [(202, 86), (219, 92), (240, 88), (257, 77), (266, 64), (263, 44),
             (250, 26), (228, 13), (200, 6), (174, 4), (148, 7), (124, 17),
             (105, 35), (93, 53), (89, 67), (94, 80), (107, 90), (128, 96),
             (146, 93), (155, 86)]
    G['head'] = smooth(run_a) + smooth(run_b, start=False) + smooth(run_c, start=False) + [('Z',)]

    # --- ears (left; right is mirrored)
    lower = [(9, 11), (18, 26), (38, 40), (60, 56), (77, 69), (90, 77)]
    ear = [('M', 114, 34), ('L', 104, 38), ('L', 91, 20), ('L', 9, 11)] \
        + smooth(lower, start=False) + [('L', 114, 76), ('Z',)]
    G['ear_l'] = ear
    G['ear_r'] = mirror(ear)
    hat = []
    for i in range(14):
        x = 57 + i * 2.25
        y0 = 33 + (x - 57) * 0.56
        hat.append(((x, y0), (x + 1.4, y0 + 14.5 - (x - 57) * 0.08)))
    G['ear_l_detail'] = (smooth([(40, 33), (56, 39), (72, 45), (87, 50)])
                         + smooth([(22, 28), (31, 31), (40, 33)])
                         + smooth([(64, 61), (69, 62.5), (74, 62.5)]))
    G['ear_l_sliver'] = smooth([(14, 19), (20, 24), (28, 28.5), (36, 31)], t=1.0) + \
                        smooth([(36, 31), (28, 31), (21, 28.5), (15, 24)], start=False) + [('Z',)]
    G['ear_l_hatch'] = lines(hat)
    G['ear_r_detail'] = mirror(G['ear_l_detail'])
    G['ear_r_sliver'] = mirror(G['ear_l_sliver'])
    G['ear_r_hatch'] = mirror(G['ear_l_hatch'])

    # --- body (funnel)
    left = [(113, 92), (116, 103), (121, 119), (125, 133), (130, 148),
            (134, 163), (135, 175), (136, 181), (135, 187), (137, 197),
            (139, 209), (143, 220), (151, 228)]
    bottom = [(151, 228), (170, 232), (188, 228)]
    right = [(188, 228), (201, 220), (205, 209), (208, 197), (210, 187), (210, 181),
             (209, 175), (211, 163), (217, 148), (224, 133), (228, 119), (233, 103),
             (240, 92)]
    G['body'] = [('M', 113, 64), ('L', 113, 92)] + smooth(left, start=False) \
        + smooth(bottom, start=False) + smooth(right, start=False) + [('L', 240, 64), ('Z',)]

    # collar hatch
    segs = []
    x = 115.0
    while x < 156:
        d = x - 113
        ln = max(7.0, 17.5 - d * 0.21)
        segs.append(((x, 93 + d * 0.0), (x + 0.9, 93 + ln)))
        x += 2.0
    x = 242.0
    while x > 202:
        d = 240 - x
        ln = max(7.0, 17.5 - d * 0.21)
        segs.append(((x, 93), (x - 0.9, 93 + ln)))
        x -= 2.0
    G['collar_hatch'] = lines(segs)

    # rim + fold lines
    G['rim'] = smooth([(127, 131), (142, 137), (160, 139.5), (176, 140), (194, 139), (210, 136), (224, 130)])
    G['fold_l1'] = smooth([(126, 127), (138, 119), (150, 113.5), (163, 111)])
    G['fold_l2'] = smooth([(128, 133), (141, 125), (153, 120), (165, 118.5)])
    G['fold_r1'] = mirror(G['fold_l1'])
    G['fold_r2'] = mirror(G['fold_l2'])
    segs = []
    x = 161.0
    while x < 195:
        h = 9 - abs(x - 178) * 0.28
        segs.append(((x, 126.5), (x + 0.4, 126.5 + h)))
        x += 1.7
    G['under_tongue'] = lines(segs)

    # band details
    G['band_line'] = smooth([(166, 187), (180, 188), (195, 185.5)])
    segs = []
    x = 142.0
    while x < 159:
        segs.append(((x, 177), (x + 1.5, 184.5)))
        x += 2.2
    x = 180.0
    while x < 205:
        segs.append(((x, 175), (x + 0.5, 175 + 6 + (x - 180) * 0.2)))
        x += 2.0
    G['band_hatch'] = lines(segs)
    G['edge_ul'] = smooth([(132, 144), (135, 158), (137, 171)])
    G['edge_ur'] = smooth([(212, 140), (210, 156), (208, 171)])
    G['edge_ll'] = smooth([(138, 192), (140, 203), (141, 212)])
    G['edge_lr'] = smooth([(204, 194), (201, 204), (198, 213)])
    G['base_arc'] = smooth([(148, 216), (160, 219.5), (176, 220.5), (188, 218)])
    segs = []
    x = 152.0
    while x < 188:
        segs.append(((x, 223), (x + 0.8, 228.5 - abs(x - 170) * 0.05)))
        x += 1.7
    G['base_hatch'] = lines(segs)

    # spout
    G['spout'] = [('M', 153, 229), ('L', 182, 229), ('L', 176.5, 254), ('L', 157, 254), ('Z',)]
    G['spout_ribs'] = lines([((154.5, 235.5), (180.5, 233.0)),
                             ((155.5, 241.5), (179.0, 239.0)),
                             ((156.2, 247.5), (177.8, 245.0))])
    G['spout_rim'] = [('M', 157, 254), ('L', 176.5, 254)]

    # --- face
    G['nose_l'] = ellipse(168.0, 54.0, 4.7, 4.4)
    G['nose_r'] = ellipse(187.0, 53.0, 3.8, 3.7)

    # tail centerline (traced from the reference)
    G['tail_pts'] = spline_points([(165, 249), (163.8, 258), (163, 268), (163, 278), (164, 285),
                                   (167, 290.5), (173, 293.5), (181, 292), (189, 288),
                                   (197, 282.5), (203, 275.5), (207.5, 269.5), (211, 264.5),
                                   (214, 259.5)], per=5)
    # spade outline traced from the reference (absolute rest-pose coords)
    e0 = G['tail_pts'][-1]
    a0 = G['tail_pts'][-3]
    ang0 = math.degrees(math.atan2(e0[1] - a0[1], e0[0] - a0[0]))
    sp_abs = smooth([(244, 235.5), (232, 236.8), (220, 236.3), (211, 238.5), (206, 242.5),
                     (205, 247), (207, 251.5), (210.5, 255.5), (213, 259.5)]) + \
             smooth([(213, 259.5), (219, 263.5), (224, 266.5), (229, 268), (234, 266),
                     (236.5, 262), (236, 257), (237.5, 252), (238.5, 246), (241, 241),
                     (244, 235.5)], start=False) + [('Z',)]
    G['spade'] = tpath(sp_abs, _chain(m_rot(-ang0), m_tr(-e0[0], -e0[1])))
    return G

G = _build()

# ----------------------------------------------------------------- tail static data
def _tail_static():
    pts = G['tail_pts']
    n = len(pts)
    norms = []
    for i in range(n):
        a = pts[max(0, i-1)]
        b = pts[min(n-1, i+1)]
        tx, ty = b[0]-a[0], b[1]-a[1]
        l = math.hypot(tx, ty) or 1.0
        norms.append((-ty/l, tx/l))
    return norms
TAIL_N = _tail_static()

def tail_shape(P):
    """returns (ribbon_path, spade_path) in body-local design coords"""
    pts = G['tail_pts']
    n = len(pts)
    amp, ph, bend, trot = P['tail_amp'], P['tail_phase'], P['tail_bend'], P['tail_rot']
    cur = []
    for i, (x, y) in enumerate(pts):
        s = i / (n - 1.0)
        d = amp * 6.2 * (s ** 1.25) * math.sin(ph - s * 4.2)
        nx, ny = TAIL_N[i]
        cur.append((x + nx*d + bend * 8.0 * (s ** 1.5), y + ny*d))
    if trot:
        r = m_rot(trot, TAIL_BASE[0], TAIL_BASE[1])
        cur = [m_pt(r, x, y) for (x, y) in cur]
    left, right = [], []
    for i in range(n):
        a = cur[max(0, i-1)]
        b = cur[min(n-1, i+1)]
        tx, ty = b[0]-a[0], b[1]-a[1]
        l = math.hypot(tx, ty) or 1.0
        nx, ny = -ty/l, tx/l
        s = i / (n - 1.0)
        hw = 4.2 - 0.5 * s
        left.append((cur[i][0] + nx*hw, cur[i][1] + ny*hw))
        right.append((cur[i][0] - nx*hw, cur[i][1] - ny*hw))
    ribbon = poly(left + right[::-1])
    e = cur[-1]
    a = cur[-3]
    ang = math.degrees(math.atan2(e[1]-a[1], e[0]-a[0]))
    m = _chain(m_tr(e[0], e[1]), m_rot(ang))
    spade = tpath(G['spade'], m)
    return ribbon, spade

# ----------------------------------------------------------------- tongue
def tongue_shape(P):
    ext = max(0.0, P['tongue'])
    sway = P['tongue_sway']
    y0 = 87.0
    L = 36.0 * ext + 8.0
    n = 9
    left, right, slit_l, slit_r = [], [], [], []
    for i in range(n + 1):
        s = i / float(n)
        cx = 178.5 + sway * 9.0 * s * s
        cy = y0 + L * s
        hw = 12.6 * (1.0 - s) ** 0.6 * min(1.0, 0.35 + ext)
        left.append((cx - hw, cy))
        right.append((cx + hw, cy))
        sw = 3.2 * (math.sin(math.pi * min(1.0, s * 0.96)) ** 0.7) * min(1.0, 0.4 + ext)
        slit_l.append((cx - sw, cy))
        slit_r.append((cx + sw, cy))
    tong = smooth(left + right[::-1], closed=True, t=0.9)
    slit = smooth(slit_l[:-1] + slit_r[::-1][1:], closed=True, t=0.9)
    return tong, slit

# ----------------------------------------------------------------- small fx glyphs
def glyph_z(size):
    w, h = size, size
    return [('M', -w/2, -h/2), ('L', w/2, -h/2), ('L', -w/2, h/2), ('L', w/2, h/2)]

def glyph_spark(size):
    s = size
    return [('M', 0, -s), ('C', 0, -s*0.2, s*0.2, 0, s, 0), ('C', s*0.2, 0, 0, s*0.2, 0, s),
            ('C', 0, s*0.2, -s*0.2, 0, -s, 0), ('C', -s*0.2, 0, 0, -s*0.2, 0, -s), ('Z',)]

# ----------------------------------------------------------------- pose
def default_pose():
    return dict(
        ox=0.0, oy=0.0, rot=0.0, sx=1.0, sy=1.0,
        head_rot=0.0, head_dy=0.0, body_rot=0.0, breathe=0.0,
        ear_l=0.0, ear_r=0.0,
        tail_amp=0.6, tail_phase=0.0, tail_bend=0.0, tail_rot=0.0,
        look_x=0.0, look_y=0.0, blink=0.0,
        tongue=0.76, tongue_sway=0.0,
        shadow=0.0, halo=1.0, affection=0.0,
        wave=0.0, wave_k=0.05, wave_ph=0.0,
        fx=[],   # list of (kind, x, y, size, alpha, rot) in design units relative to window centre
    )

def glyph_heart(s):
    return [('M', 0, s*0.95), ('C', -s*1.25, s*0.1, -s*0.85, -s*0.95, 0, -s*0.4),
            ('C', s*0.85, -s*0.95, s*1.25, s*0.1, 0, s*0.95), ('Z',)]

def glyph_bulb(s):
    """lightbulb: glass circle with a two-line screw base"""
    return (ellipse(0, -s*0.25, s*0.72, s*0.82)
            + [('M', -s*0.32, s*0.62), ('L', s*0.32, s*0.62), ('M', -s*0.24, s*0.86), ('L', s*0.24, s*0.86)])

def glyph_rays(s):
    """five radiating strokes above a bulb - the 'idea' shine"""
    out = []
    for a in (-90, -50, -130, -10, -170):
        r = math.radians(a)
        out += [('M', math.cos(r)*s*1.15, -s*0.25 + math.sin(r)*s*1.15),
                ('L', math.cos(r)*s*1.6, -s*0.25 + math.sin(r)*s*1.6)]
    return out

def glyph_ring(s):
    return ellipse(0, 0, s, s*0.72)

def glyph_drop(s):
    return [('M', 0, -s), ('C', s*0.35, -s*0.4, s*0.85, s*0.1, s*0.85, s*0.5),
            ('C', s*0.85, s*1.0, -s*0.85, s*1.0, -s*0.85, s*0.5),
            ('C', -s*0.85, s*0.1, -s*0.35, -s*0.4, 0, -s), ('Z',)]

def glyph_bang(s):
    """(stroke path, dot path)  - an exclamation mark"""
    return [('M', 0, -s), ('C', s*0.12, -s*0.4, s*0.1, 0.0, 0, s*0.38)], ellipse(0, s*0.9, s*0.17, s*0.17)

def glyph_query(s):
    """(stroke path, dot path)  - a question mark"""
    return ([('M', -s*0.55, -s*0.5), ('C', -s*0.55, -s*1.15, s*0.65, -s*1.15, s*0.65, -s*0.4),
             ('C', s*0.65, s*0.12, 0, s*0.12, 0, s*0.52)], ellipse(0, s*1.0, s*0.17, s*0.17))

def glyph_anger(s):
    """four small corner brackets - the classic vein / anger mark"""
    segs = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            segs.append(((sx*s, sy*s*0.35), (sx*s*0.35, sy*s*0.35)))
            segs.append(((sx*s*0.35, sy*s*0.35), (sx*s*0.35, sy*s)))
    return lines(segs)

# ---- animation-pack glyphs (long-form fx kinds used by pet_anims clips)
def glyph_heart_fx(s):
    """pack heart: wider lobes than the host reaction heart (matches the pack demo)"""
    return [('M', 0, s*0.95), ('C', -s*1.5, -s*0.05, -s*0.75, -s*1.2, 0, -s*0.4),
            ('C', s*0.75, -s*1.2, s*1.5, -s*0.05, 0, s*0.95), ('Z',)]

def glyph_bang_fx(s):
    return [('M', 0, -s*1.25), ('L', 0, s*0.2), ('M', 0, s*0.75), ('L', 0, s*0.8)]

def glyph_quest_fx(s):
    return [('M', -s*0.65, -s*0.75), ('C', -s*0.65, -s*1.6, s*0.85, -s*1.6, s*0.85, -s*0.65),
            ('C', s*0.85, -s*0.1, 0, -s*0.15, 0, s*0.35), ('M', 0, s*0.85), ('L', 0, s*0.9)]

def glyph_note_stem(s):
    return [('M', s*0.45, s*0.7), ('L', s*0.45, -s*1.1), ('C', s*0.5, -s*0.6, s*1.2, -s*0.7, s*1.1, -s*0.1)]

def glyph_star(s):
    pts = []
    for i in range(10):
        a = math.radians(-90 + i * 36)
        r = s * (1.0 if i % 2 == 0 else 0.42)
        pts.append((math.cos(a) * r, math.sin(a) * r))
    return poly(pts)

def glyph_zap(s):
    return poly([(s*0.25, -s*1.3), (-s*0.6, s*0.1), (-s*0.05, s*0.1), (-s*0.3, s*1.3), (s*0.65, -s*0.25), (s*0.1, -s*0.25)])

def glyph_cloud(s):
    """thought-bubble blobs; the trailing dots are placed by the caller"""
    return [ellipse(bx*s, by*s, rx*s, ry*s)
            for (bx, by, rx, ry) in ((-0.9, 0.1, 0.9, 0.62), (0.0, -0.35, 1.05, 0.8),
                                      (0.95, 0.05, 0.9, 0.64), (0.1, 0.35, 1.2, 0.55))]

def glyph_drop_fx(s):
    """pack sweat/water drop: pointed top, round bottom (the short 'd' keeps its own shape)"""
    return [('M', 0, -s*1.1), ('C', s*0.95, s*0.1, s*0.7, s*0.95, 0, s*0.95),
            ('C', -s*0.7, s*0.95, -s*0.95, s*0.1, 0, -s*1.1), ('Z',)]

FX_RGB = {'h': (238, 84, 116), 'd': (74, 128, 206), 'heart': (238, 84, 116)}

# Pack fx kinds use long-form names (heart/bang/drop/...); host reaction cues
# keep their short letter codes (h/b/d/...).  They are deliberately separate,
# so each renderer branch can keep its own halo/shape rules.
PACK_FX_KINDS = frozenset(('heart', 'bulb', 'ring', 'bang', 'quest', 'note', 'drop', 'star', 'zap', 'cloud'))

# ---- affection tint (deterministic; never waits on anything)
BLUSH_LIGHT = (255, 176, 192)
BLUSH_DEEP = (228, 56, 84)
BLUSH_CHEEKS = ((111.0, 69.0, 17.0, 9.5, -14.0), (247.0, 67.5, 17.0, 9.5, 14.0))
BLUSH_MUZZLE = (178.0, 72.0, 21.0, 6.0, 0.0)

def _mixc(a, b, t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))

def blush_style(aff):
    """affection 0..1 -> (rgb, alpha 0..1, deepness 0..1) or None below the threshold"""
    a = round(max(0.0, min(1.0, aff)) * 50.0) / 50.0          # quantised: bounded set of colours
    if a < 0.08:
        return None
    k = min(1.0, (a - 0.08) / 0.92)
    deep = max(0.0, min(1.0, (a - 0.42) / 0.5))
    deep = deep * deep * (3.0 - 2.0 * deep)
    return _mixc(BLUSH_LIGHT, BLUSH_DEEP, deep), 0.16 + 0.66 * k, deep

def draw_blush(cv, aff, MH, w):
    bs = blush_style(aff)
    if bs is None:
        return
    rgb, al, deep = bs
    spots = list(BLUSH_CHEEKS) + [BLUSH_MUZZLE]
    for (cx, cy, rx, ry, rot) in spots:
        gain = 0.55 if (cx, cy) == BLUSH_MUZZLE[:2] else 1.0      # muzzle patch stays gentler
        for sc, fa in ((1.0, 0.34), (0.74, 0.40), (0.48, 0.50)):
            cv.fill(tpath(ellipse(cx, cy, rx*sc, ry*sc, rot), MH), rgb + (int(255 * min(1.0, al * fa * gain)),))
    if deep > 0.2:                                               # anime hatch marks once he is properly flustered
        segs = []
        for (cx, cy, rx, ry, rot) in BLUSH_CHEEKS:
            for i in (-1, 0, 1):
                x = cx + i * 5.5
                segs.append(((x - 1.6, cy - 3.6), (x + 1.6, cy + 3.8)))
        cv.stroke(tpath(lines(segs), MH), rgb + (int(215 * deep),), w(1.3))

def tongue_colors(aff):
    """paper -> light pink fill and ink -> warm ink outline as affection rises"""
    t = max(0.0, min(1.0, aff))
    t = round(t * 50.0) / 50.0
    fill = _mixc(PAPER[:3], (255, 196, 208), min(0.7, t * 0.85)) + (255,)
    ink = _mixc(INK[:3], (150, 28, 58), min(0.55, t * 0.6)) + (255,)
    return fill, ink

# ---- procedural sounds (no files shipped; tiny wavs are synthesised on first use)
def synth_wav(kind, pitch=1.0, bright=0.3, soft=0.0, rate=22050):
    """kind -> WAV bytes.  pitch / bright / soft are the (bounded) disposition colouring."""
    import io, wave, struct
    recipes = {            # (f0, f1, start, dur) per note
        'click':   [(520, 640, 0.00, 0.085)],
        'double':  [(587, 640, 0.00, 0.06), (784, 880, 0.075, 0.085)],
        'grab':    [(330, 390, 0.00, 0.06)],
        'land':    [(240, 190, 0.00, 0.09)],
        'throw':   [(760, 300, 0.00, 0.19)],
        'startle': [(880, 1240, 0.00, 0.10)],
    }
    notes = recipes.get(kind, recipes['click'])
    total = max(n[2] + n[3] for n in notes) + 0.04
    N = int(rate * total)
    buf = [0.0] * N
    for (f0, f1, st, du) in notes:
        i0, ln = int(st * rate), int(du * rate)
        att = 0.004 + 0.012 * soft
        ph = 0.0
        for i in range(ln):
            u = i / float(ln)
            f = (f0 + (f1 - f0) * u) * pitch
            ph += 2.0 * math.pi * f / rate
            t = i / float(rate)
            env = min(1.0, t / att) * math.exp(-t / (du * (0.34 + 0.2 * soft)))
            s = math.sin(ph) + bright * 0.45 * math.sin(2.0 * ph) + bright * 0.15 * math.sin(3.0 * ph)
            if i0 + i < N:
                buf[i0 + i] += s * env
    peak = max(1e-6, max(abs(x) for x in buf))
    amp = 0.22 * 32767.0 / peak
    out = io.BytesIO()
    wf = wave.open(out, 'wb')
    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
    wf.writeframes(b''.join(struct.pack('<h', int(max(-32767, min(32767, x * amp)))) for x in buf))
    wf.close()
    return out.getvalue()


# The animation pack names its own cues; reuse the host banks so the pet's
# sound set stays consistent and an unknown cue can never silence the pack.
ANIM_SOUND_ALIAS = {
    'hic': 'click', 'gasp': 'click', 'hmph': 'click', 'yawn': 'click', 'gulp': 'click',
    'cheer': 'double', 'tada': 'double', 'ding': 'double',
    'zap': 'startle', 'crack': 'startle',
}

class SoundBank(object):
    """Local user-supplied clip banks with shuffle bags and gentle event cooldowns.

    Clips are pre-cut WAV copies in ``sounds/``.  Playing them never edits the
    supplied originals, and a bag is exhausted before any clip may repeat.
    ``winsound`` only offers one lightweight channel, so cooldowns deliberately
    avoid cutting one cute sound off with another.
    """
    def __init__(self, root):
        self.root = root
        self.bags = {}
        self.last = {}
        self.next_ok = {}
        self._scaled = {}
        self.cooldowns = dict(sleep=3.4, click=0.65, talk=0.85, wobble=0.34,
                              minecraft=0.52, grab=0.34, land=0.34,
                              throw=0.52, startle=0.52, double=0.42)

    def _clips(self, bank):
        if bank == 'sleep':
            names = ['sleep-purr.wav']
        elif bank in ('click', 'talk'):
            names = ['click-meow.wav']
        elif bank in ('wobble', 'grab', 'land', 'throw'):
            names = ['wobble-%02d.wav' % i for i in range(1, 7)]
        else:
            names = ['minecraft-%02d.wav' % i for i in range(1, 27)]
        return [os.path.join(self.root, n) for n in names
                if os.path.isfile(os.path.join(self.root, n))]

    def pick(self, kind):
        now = time.monotonic()
        if now < self.next_ok.get(kind, 0.0):
            return None
        bank = ('sleep' if kind == 'sleep' else
                'talk' if kind == 'talk' else
                'click' if kind == 'click' else
                # The user sound-mapped Wobble specifically to carrying/moving.
                'wobble' if kind in ('grab', 'land', 'throw') else
                'minecraft')
        bag = self.bags.get(bank, [])
        if not bag:
            bag = self._clips(bank)
            if len(bag) > 1:
                random.shuffle(bag)
                previous = self.last.get(bank)
                if previous is not None and bag[0] == previous:
                    bag.append(bag.pop(0))
            self.bags[bank] = bag
        if not bag:
            return None
        clip = bag.pop(0)
        self.last[bank] = clip
        self.next_ok[kind] = now + self.cooldowns.get(kind, 0.5)
        return clip

    def wave_data(self, clip, volume):
        """Return an in-memory scaled WAV; source clips on disk remain untouched."""
        level = max(0, min(100, int(volume)))
        if level >= 100:
            with open(clip, 'rb') as f:
                return f.read()
        key = (clip, level)
        cached = self._scaled.get(key)
        if cached is not None:
            return cached
        with open(clip, 'rb') as f:
            self._scaled[key] = self.scale_wav_data(f.read(), level)
        return self._scaled[key]

    def playable_path(self, clip, volume):
        """A filename for winsound's asynchronous API (it rejects SND_MEMORY).

        Volume-adjusted copies live in the OS temp cache, never beside or over
        the supplied assets. At 100% the original extracted clip is used.
        """
        level = max(0, min(100, int(volume)))
        if level >= 100:
            return clip
        import tempfile
        folder = os.path.join(tempfile.gettempdir(), 'chill_pet_sound_cache')
        try: os.makedirs(folder, exist_ok=True)
        except Exception: return clip
        stem = os.path.splitext(os.path.basename(clip))[0]
        target = os.path.join(folder, '%s-%d.wav' % (stem, level))
        if not os.path.isfile(target):
            with open(target, 'wb') as f: f.write(self.wave_data(clip, level))
        return target

    def playable_raw_path(self, name, raw, volume):
        import tempfile
        folder = os.path.join(tempfile.gettempdir(), 'chill_pet_sound_cache')
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, '%s-%d.wav' % (name, max(0, min(100, int(volume)))))
        if not os.path.isfile(target):
            with open(target, 'wb') as f: f.write(self.scale_wav_data(raw, volume))
        return target

    @staticmethod
    def scale_wav_data(raw, volume):
        if int(volume) >= 100:
            return raw
        import io, wave, audioop
        srcbuf = io.BytesIO(raw)
        with wave.open(srcbuf, 'rb') as src:
            params = src.getparams()
            frames = src.readframes(src.getnframes())
        frames = audioop.mul(frames, params.sampwidth, max(0, int(volume)) / 100.0)
        out = io.BytesIO()
        with wave.open(out, 'wb') as dst:
            dst.setparams(params); dst.writeframes(frames)
        return out.getvalue()


LW_MAIN, LW_DETAIL, LW_HATCH = 2.9, 1.6, 1.15

def _chain(*ms):
    r = ms[0]
    for m in ms[1:]:
        r = m_mul(r, m)
    return r

def draw_pet(cv, P, W, H, S):
    """Draw the pet on canvas cv (fill/stroke). W,H = window px, S = design->px."""
    base = _chain(m_tr(W/2.0 + P['ox'], H/2.0 + P['oy']), m_sc(S), m_rot(P['rot']),
                  m_sc(P['sx'], P['sy']), m_tr(-CX, -CY))
    MH = _chain(base, m_tr(0, P['head_dy']), m_rot(P['head_rot'], NECK[0], NECK[1]))
    MB = _chain(base, m_rot(P['body_rot'], NECK[0], NECK[1]),
                m_sc(1.0, 1.0 + P['breathe'], NECK[0], NECK[1]))
    MEL = _chain(MH, m_rot(P['ear_l'], *EAR_PIV_L))
    MER = _chain(MH, m_rot(-P['ear_r'], *EAR_PIV_R))
    fxM = _chain(m_tr(W/2.0 + P['ox'], H/2.0 + P['oy']), m_sc(S))
    w = lambda x: x * S

    # ---- jelly warp (additive sine displacement from animation clips).
    # Amplitude is declared in design px; every transformed path goes through
    # _tp(), which displaces in device px after the full CTM.
    _wv = P.get('wave', 0.0)
    if _wv and abs(_wv) > 0.004:
        _wk, _wph, _A = P.get('wave_k', 0.05), P.get('wave_ph', 0.0), _wv * S
        _cy = H / 2.0 + P['oy']
        def _tp(path, m):
            out = []
            for cmd in tpath(path, m):
                if cmd[0] == 'Z':
                    out.append(cmd); continue
                v = list(cmd[1:])
                for i in range(0, len(v), 2):
                    dy = (v[i+1] - _cy) / S
                    grow = 0.4 + 0.6 * min(1.0, max(0.0, (dy + 150.0) / 300.0))
                    v[i] += _A * grow * math.sin(_wk * dy + _wph)
                out.append((cmd[0],) + tuple(v))
            return out
    else:
        _tp = tpath

    # ---- shadow (window space)
    if P['shadow'] > 0.02:
        a = P['shadow']
        cx = W/2.0 + P['ox']
        cy = H/2.0 + 157.0*S
        for k, (rx, al) in enumerate([(62, 0.35), (50, 0.5), (38, 0.7)]):
            col = (12, 12, 14, int(70 * a * al))
            cv.fill(ellipse(cx, cy, rx*S*(0.9 + 0.1*a), 7.5*S*(0.6 + 0.4*a)*(1 - k*0.12)), col)

    # ---- tail
    ribbon, spade = tail_shape(P)
    ribbon = _tp(ribbon, MB)
    spade = _tp(spade, MB)

    head = _tp(G['head'], MH)
    earl = _tp(G['ear_l'], MEL)
    earr = _tp(G['ear_r'], MER)
    body = _tp(G['body'], MB)
    spout = _tp(G['spout'], MB)

    # ---- halo / sticker outline
    if P['halo'] > 0.01:
        hw = w(7.0 * P['halo'])
        for pth in (ribbon, spade, earl, earr, spout, body, head):
            cv.fill(pth, HALO)
            cv.stroke(pth, HALO, hw)

    # ---- tail
    cv.fill(ribbon, INK)
    cv.fill(spade, INK)
    cv.stroke(spade, INK, w(1.2))

    # ---- ears
    for pth, M, s in ((earl, MEL, 'l'), (earr, MER, 'r')):
        cv.fill(pth, PAPER)
        cv.fill(_tp(G['ear_%s_sliver' % s], M), INK)
        cv.stroke(_tp(G['ear_%s_detail' % s], M), INK, w(LW_DETAIL))
        cv.stroke(_tp(G['ear_%s_hatch' % s], M), INK, w(LW_HATCH))
        cv.stroke(pth, INK, w(LW_MAIN + 0.4))

    # ---- body
    cv.fill(spout, PAPER)
    cv.stroke(_tp(G['spout_ribs'], MB), INK, w(1.7))
    cv.stroke(spout, INK, w(2.1))
    cv.fill(body, PAPER)
    cv.stroke(_tp(G['collar_hatch'], MB), INK, w(LW_HATCH))
    for k in ('band_hatch', 'under_tongue', 'base_hatch'):
        cv.stroke(_tp(G[k], MB), INK, w(LW_HATCH))
    for k in ('rim', 'fold_l1', 'fold_l2', 'fold_r1', 'fold_r2', 'band_line',
              'edge_ul', 'edge_ur', 'edge_ll', 'edge_lr', 'base_arc'):
        cv.stroke(_tp(G[k], MB), INK, w(LW_DETAIL))
    cv.stroke(body, INK, w(LW_MAIN))

    # ---- tongue (head frame, under the muzzle lobes)
    tong, slit = tongue_shape(P)
    tfill, tink = tongue_colors(P.get('affection', 0.0))
    cv.fill(_tp(tong, MH), tfill)
    cv.stroke(_tp(tong, MH), tink, w(1.9))
    cv.fill(_tp(slit, MH), INK)

    # ---- head
    cv.fill(head, PAPER)
    cv.stroke(head, INK, w(LW_MAIN + 0.2))
    draw_blush(cv, P.get('affection', 0.0), MH, w)

    # ---- eyes
    bl = min(1.0, max(0.0, P['blink']))
    lx, ly = P['look_x'], P['look_y']
    for (ecx, ecy, tilt), side in ((EYE_L, 1.0), (EYE_R, -1.0)):
        outer = _tp(ellipse(ecx, ecy, EYE_RX, EYE_RY, tilt), MH)
        cv.fill(outer, PAPER)
        if bl < 0.55:
            # rim hatch (top arc)
            segs = []
            t = math.radians(tilt)
            ct, st = math.cos(t), math.sin(t)
            for i in range(17):
                a = math.radians(200 + i * 8.5)
                for (k0, k1) in ((0.97, 0.80),):
                    x0, y0 = EYE_RX*k0*math.cos(a), EYE_RY*k0*math.sin(a)
                    x1, y1 = EYE_RX*k1*math.cos(a), EYE_RY*k1*math.sin(a)
                    segs.append(((ecx + ct*x0 - st*y0, ecy + st*x0 + ct*y0),
                                 (ecx + ct*x1 - st*y1, ecy + st*x1 + ct*y1)))
            cv.stroke(_tp(lines(segs), MH), INK, w(0.9))
        cv.stroke(outer, INK, w(1.7))
        px = ecx + side * 1.0 + lx * 1.9
        py = ecy + 1.3 + ly * 1.5
        if bl >= 0.8:
            t = math.radians(tilt)
            ct, st = math.cos(t), math.sin(t)
            pts = []
            for u in (-1.0, -0.5, 0.0, 0.5, 1.0):
                x0 = u * 16.5
                y0 = 4.2 * (1.0 - u * u) - 1.0
                pts.append((px + ct*x0 - st*y0, py + st*x0 + ct*y0))
            cv.stroke(_tp(smooth(pts), MH), INK, w(3.2))
        else:
            ry = max(0.9, 12.8 * (1.0 - 0.92 * bl))
            pupil = _tp(ellipse(px, py, 21.0, ry, tilt), MH)
            cv.fill(pupil, INK)

    # ---- nose
    cv.fill(_tp(G['nose_l'], MH), INK)
    cv.fill(_tp(G['nose_r'], MH), INK)

    # ---- fx glyphs
    for (kind, x, y, size, alpha, rot) in P['fx']:
        al = max(0.0, min(1.0, alpha))
        col = FX_RGB.get(kind, (12, 12, 14)) + (int(255 * al),)
        m = _chain(fxM, m_tr(x, y), m_rot(rot))
        if kind in ('ps', 'pz'):
            # pack-owned spark / sleep Z: same halo treatment as the demo
            hal = (255, 255, 255, int(235 * al))
            if kind == 'pz':
                pth = tpath(glyph_z(size), m)
                cv.stroke(pth, hal, w(6.0)); cv.stroke(pth, col, w(2.4))
            else:
                pth = tpath(glyph_spark(size), m)
                cv.stroke(pth, hal, w(3.5)); cv.fill(pth, col)
        elif kind == 'z':
            cv.stroke(tpath(glyph_z(size), m), col, w(2.4))
        elif kind == 'h':
            cv.fill(tpath(glyph_heart(size), m), col)
        elif kind == 'd':
            cv.fill(tpath(glyph_drop(size), m), col)
        elif kind in ('b', 'q'):
            st, dot = (glyph_bang if kind == 'b' else glyph_query)(size)
            cv.stroke(tpath(st, m), col, w(2.6))
            cv.fill(tpath(dot, m), col)
        elif kind == 'a':
            cv.stroke(tpath(glyph_anger(size), m), col, w(2.1))
        elif kind in ('heart', 'bulb', 'ring'):
            # animation-pack glyphs: ink shape over a white halo, so they read
            # on dark wallpapers too (the halo never covers the shape itself)
            hal = (255, 255, 255, int(235 * al))
            if kind == 'heart':
                pth = tpath(glyph_heart_fx(size), m)
                cv.stroke(pth, hal, w(4.5)); cv.fill(pth, col)
            elif kind == 'ring':
                pth = tpath(glyph_ring(size), m)
                cv.stroke(pth, hal, w(5.0)); cv.stroke(pth, col, w(2.0))
            else:
                pth = tpath(glyph_bulb(size), m)
                rays = tpath(glyph_rays(size), m)
                cv.stroke(rays, hal, w(5.0)); cv.stroke(rays, col, w(1.8))
                cv.fill(tpath(ellipse(0, -size*0.25, size*0.72, size*0.82), m), (255, 255, 255, int(255 * al)))
                cv.stroke(pth, hal, w(5.0)); cv.stroke(pth, col, w(2.0))
        elif kind in ('bang', 'quest'):
            pth = tpath(glyph_bang_fx(size) if kind == 'bang' else glyph_quest_fx(size), m)
            cv.stroke(pth, (255, 255, 255, int(235 * al)), w(7.0)); cv.stroke(pth, col, w(3.0))
        elif kind == 'note':
            head = tpath(ellipse(0, size*0.75, size*0.62, size*0.42, -20), m)
            stem = tpath(glyph_note_stem(size), m)
            hal = (255, 255, 255, int(235 * al))
            cv.stroke(stem, hal, w(6.0)); cv.stroke(head, hal, w(4.0))
            cv.stroke(stem, col, w(2.2)); cv.fill(head, col)
        elif kind == 'drop':
            # pack sweat/water drop: haloed white fill, ink outline
            pth = tpath(glyph_drop_fx(size), m)
            cv.stroke(pth, (255, 255, 255, int(235 * al)), w(5.0))
            cv.fill(pth, (255, 255, 255, int(255 * al))); cv.stroke(pth, col, w(1.8))
        elif kind == 'star':
            pth = tpath(glyph_star(size), m)
            cv.stroke(pth, (255, 255, 255, int(235 * al)), w(4.5)); cv.fill(pth, col)
        elif kind == 'zap':
            pth = tpath(glyph_zap(size), m)
            cv.stroke(pth, (255, 255, 255, int(235 * al)), w(5.0)); cv.fill(pth, col)
        elif kind == 'cloud':
            hal = (255, 255, 255, int(235 * al))
            paper = (255, 255, 255, int(255 * al))
            ps = [tpath(q, m) for q in glyph_cloud(size)]
            for q in ps: cv.stroke(q, hal, w(7.0))
            for q in ps: cv.stroke(q, col, w(2.4))
            for q in ps: cv.fill(q, paper)
            for (dx_, dy_, r_) in ((-1.5, 1.35, 0.32), (-2.1, 1.9, 0.2)):
                q = tpath(ellipse(dx_*size, dy_*size, r_*size, r_*size), m)
                cv.stroke(q, hal, w(5.0)); cv.fill(q, paper); cv.stroke(q, col, w(1.8))
        else:
            pth = tpath(glyph_spark(size), m)
            cv.fill(pth, col)


# ============================================================================
#  Behaviour brain
# ============================================================================
import random

DESIGN_W, DESIGN_H = 460.0, 440.0
EXT_L = EXT_R = 175.0   # visible half-width of the character (design px)
EXT_T, EXT_B = 150.0, 160.0

def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)

def lerp(a, b, t):
    return a + (b - a) * t

def smoothstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)

class Spring(object):
    __slots__ = ('x', 'v', 'k', 'c', 't', 'k0', 'c0')
    def __init__(self, x=0.0, k=120.0, c=12.0):
        self.x = x; self.v = 0.0; self.k = k; self.c = c; self.t = x; self.k0 = k; self.c0 = c
    def step(self, dt):
        n = max(1, int(dt / 0.006) + 1)
        h = dt / n
        k, c, t = self.k, self.c, self.t
        x, v = self.x, self.v
        for _ in range(n):
            v += (k * (t - x) - c * v) * h
            x += v * h
        self.x, self.v = x, v
    def kick(self, dv):
        self.v += dv

# ============================================================================
#  Reaction layer (optional + additive).
#  Everything in this section is inert until the "Let him think" switch is on.
#  With it off the pet behaves exactly like before.
# ============================================================================
import os, sys, time, json, threading, queue, datetime, http.server

STATE_PATH = os.path.join(os.path.expanduser('~'), '.chill_pet_state.json')
DISP_KEYS = ('sleepy', 'mischievous', 'affectionate')
DISP_FX = 0.18          # every disposition effect is bounded to roughly +-18 %

def neutral_disp():
    return dict((k, 1.0 / 3.0) for k in DISP_KEYS)

def norm_disp(d):
    v = []
    for k in DISP_KEYS:
        try:
            x = float(d.get(k, 1.0 / 3.0))
        except Exception:
            x = 1.0 / 3.0
        v.append(max(0.02, x))
    s = sum(v)
    return dict(zip(DISP_KEYS, [x / s for x in v]))

def disp_biases(d):
    """disposition -> per-trait bias in [-1, 1] (0 = neutral)"""
    return dict((k, clamp((d[k] - 1.0 / 3.0) * 3.0, -1.0, 1.0)) for k in DISP_KEYS)

def disp_word(d):
    k = max(DISP_KEYS, key=lambda x: d[x])
    return 'balanced' if d[k] - 1.0 / 3.0 < 0.07 else k


# ----------------------------------------------------------------- persisted state
class PetState(object):
    """Small JSON file (~/.chill_pet_state.json). The model keeps nothing itself:
    this summary is re-read from disk before every generation and fed back in."""
    HIST_MAX = 8
    SAVE_EVERY = 4.0

    def __init__(self, path=STATE_PATH, clock=time.time):
        self.path = path
        self.clock = clock
        self._mtime = None
        self._dirty = False
        self._last_save = -1e9
        self._reset()
        self.load()

    def _today(self, now=None):
        return datetime.date.fromtimestamp(self.clock() if now is None else now).isoformat()

    def _reset(self):
        now = self.clock()
        self.disp = neutral_disp()
        self.disp_ts = now
        self.day = self._today(now)
        self.count = 0
        self.first_ts = None
        self.last_ts = None
        self.longest_nap = 0.0
        self.longest_perch = 0.0
        self.history = []
        # Compact local world model: categories and nicknames only, never app contents.
        self.comfort = 0.50
        self.wary = 0.0
        self.last_app = 'desktop'
        self.places = []
        self.achievements = []
        self.fresh = False

    @staticmethod
    def _num(v, default=None):
        try:
            f = float(v)
            return f if f == f and abs(f) < 1e12 else default
        except Exception:
            return default

    def load(self):
        d = None
        try:
            with open(self.path, 'r') as f:
                d = json.load(f)
            self._mtime = os.path.getmtime(self.path)
        except Exception:
            d = None
        if isinstance(d, dict):
            self._reset()
            try:
                if isinstance(d.get('disp'), dict):
                    self.disp = norm_disp(d['disp'])
                self.disp_ts = self._num(d.get('disp_ts'), self.disp_ts)
                self.day = str(d.get('day') or self.day)
                self.count = int(self._num(d.get('count'), 0))
                self.first_ts = self._num(d.get('first_ts'))
                self.last_ts = self._num(d.get('last_ts'))
                self.longest_nap = self._num(d.get('longest_nap'), 0.0)
                self.longest_perch = self._num(d.get('longest_perch'), 0.0)
                self.fresh = bool(d.get('fresh'))
                self.comfort = clamp(self._num(d.get('comfort'), 0.50), 0.0, 1.0)
                self.wary = clamp(self._num(d.get('wary'), 0.0), 0.0, 1.0)
                self.last_app = str(d.get('last_app') or 'desktop')[:16]
                self.places = [dict(name=str(x.get('name','a quiet spot'))[:24], kind=str(x.get('kind','desktop'))[:16], visits=int(self._num(x.get('visits'),1))) for x in (d.get('places') or [])[-4:] if isinstance(x,dict)]
                self.achievements = [str(x)[:32] for x in (d.get('achievements') or [])[-16:]]
                hist = []
                for h in (d.get('history') or [])[-self.HIST_MAX:]:
                    if isinstance(h, dict):
                        hist.append(dict(t=self._num(h.get('t'), 0.0), trig=str(h.get('trig', ''))[:24],
                                         act=str(h.get('act', ''))[:32], feel=str(h.get('feel') or '')[:16]))
                self.history = hist
            except Exception:
                self._reset()
        self.roll()
        return d is not None

    def roll(self, now=None):
        """day rollover + slow overnight decay of the disposition toward neutral"""
        now = self.clock() if now is None else now
        today = self._today(now)
        if today != self.day:
            self.day = today
            self.count = 0
            self.first_ts = None
            self.longest_nap = self.longest_perch = 0.0
            self._dirty = True
        gap_h = max(0.0, (now - self.disp_ts) / 3600.0)
        if gap_h >= 6.0:
            f = math.exp(-gap_h / 14.0)
            n = 1.0 / 3.0
            self.disp = norm_disp(dict((k, n + (self.disp[k] - n) * f) for k in DISP_KEYS))
            self.disp_ts = now
            self.fresh = True
            self._dirty = True
            return True
        return False

    def sync(self):
        """flush, then really read the file back (so what the prompt sees is what is on disk)"""
        self.save(force=True)
        self.load()

    def save(self, force=False):
        if not (self._dirty or force):
            return
        mono = time.monotonic()
        if not force and mono - self._last_save < self.SAVE_EVERY:
            return
        data = dict(v=1, disp=self.disp, disp_ts=self.disp_ts, day=self.day, count=self.count,
                    first_ts=self.first_ts, last_ts=self.last_ts, longest_nap=round(self.longest_nap, 1),
                    longest_perch=round(self.longest_perch, 1), fresh=self.fresh,
                    history=self.history[-self.HIST_MAX:], comfort=round(self.comfort, 3), wary=round(self.wary, 3),
                    last_app=self.last_app, places=self.places[-4:], achievements=self.achievements[-16:])
        tmp = self.path + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
            self._mtime = os.path.getmtime(self.path)
            self._dirty = False
            self._last_save = mono
        except Exception:
            pass

    # ---- updates
    def since_last(self):
        return None if self.last_ts is None else max(0.0, self.clock() - self.last_ts)

    def touch(self):
        now = self.clock()
        self.roll(now)
        self.count += 1
        if self.first_ts is None:
            self.first_ts = now
        self.last_ts = now
        if self.count >= 100: self.earn('100 gentle pets')
        if datetime.datetime.fromtimestamp(now).hour >= 23: self.earn('night owl')
        self._dirty = True

    def record_background_interaction(self):
        """Keep continuity while optional thinking is off, without inventing an LLM action."""
        self.touch()
        self.fresh = False
        self._dirty = True
        self.save()

    def note_span(self, kind, secs):
        if kind == 'sleep' and secs > self.longest_nap:
            self.longest_nap = secs; self._dirty = True
        elif kind == 'perch' and secs > self.longest_perch:
            self.longest_perch = secs; self._dirty = True
        if kind == 'perch' and secs >= 60: self.earn('first long perch')
        if kind == 'sleep' and secs >= 180: self.earn('deep nap')

    def earn(self, name):
        if name not in self.achievements:
            self.achievements.append(name); self.achievements = self.achievements[-16:]
            self._dirty = True

    def apply_feeling(self, feel, a=0.07):
        """exponential moving average of the disposition toward the feeling of what just happened"""
        tgt = neutral_disp() if feel not in DISP_KEYS else dict((k, 1.0 if k == feel else 0.0) for k in DISP_KEYS)
        self.disp = norm_disp(dict((k, self.disp[k] * (1.0 - a) + tgt[k] * a) for k in DISP_KEYS))
        self.disp_ts = self.clock()
        self.fresh = False
        self._dirty = True

    def add_history(self, trig, act, feel):
        self.history.append(dict(t=round(self.clock(), 1), trig=trig, act=act, feel=feel or ''))
        self.history = self.history[-self.HIST_MAX:]
        self._dirty = True

    def recent_actions(self, n=3):
        return [h['act'] for h in self.history[-n:] if h.get('act')]

    def observe_world(self, app_kind, gentle=False, rough=False):
        """Persist only a category-level summary—no titles, text, or app contents."""
        self.last_app = str(app_kind or 'desktop')[:16]
        if gentle:
            self.comfort = clamp(self.comfort + 0.035 * (1.0 - self.comfort), 0.0, 1.0)
            self.wary = max(0.0, self.wary - 0.05)
        if rough:
            self.comfort = max(0.0, self.comfort - 0.055)
            self.wary = clamp(self.wary + 0.12 * (1.0 - self.wary), 0.0, 1.0)
        self._dirty = True

    def note_place(self, name, kind):
        name, kind = str(name)[:24], str(kind or 'desktop')[:16]
        for p in self.places:
            if p['name'] == name:
                p['visits'] = min(99, int(p.get('visits', 0)) + 1); self._dirty = True; return
        self.places.append(dict(name=name, kind=kind, visits=1))
        self.places = self.places[-4:]
        self._dirty = True

    def memory_card(self):
        favorites = ', '.join(p['name'] for p in sorted(self.places, key=lambda x: -x.get('visits', 0))[:2]) or 'none yet'
        return dict(app=self.last_app, comfort=round(self.comfort, 2), wary=round(self.wary, 2), places=favorites)


# ----------------------------------------------------------------- opt-in local companion bridge
class CompanionBridge(object):
    """A loopback-only event receiver for the supplied Brave/VS Code add-ons.

    It accepts a tiny whitelist of state changes and never accepts code, page
    text, URLs, titles, keystrokes, clicks, or any command to control an app.
    The bridge is off unless enabled from the pet menu.
    """
    PORT = 8765
    ALLOWED = {
        'brave': set(('youtube_play', 'youtube_pause', 'youtube_music', 'youtube_fullscreen', 'youtube_browse', 'youtube_liked')),
        'vscode': set(('saved', 'build_ok', 'build_error', 'focus', 'document_context')),
    }
    def __init__(self):
        self.events = queue.Queue(32)
        self.server = None
        self.thread = None
        self.like_requested = False

    def start(self):
        if self.server is not None:
            return True
        bridge = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def _headers(self, code=200):
                self.send_response(code)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
            def do_OPTIONS(self): self._headers(204)
            def do_GET(self):
                if self.path != '/v1/command': self._headers(404); return
                like = bridge.like_requested
                bridge.like_requested = False
                self._headers(200); self.wfile.write(json.dumps({'like': like}).encode('utf-8'))
            def do_POST(self):
                if self.path != '/v1/event': self._headers(404); return
                try:
                    n = int(self.headers.get('Content-Length', '0'))
                    if n < 1 or n > 1024: raise ValueError()
                    d = json.loads(self.rfile.read(n).decode('utf-8'))
                    src, event = str(d.get('source','')), str(d.get('event',''))
                    if src not in bridge.ALLOWED or event not in bridge.ALLOWED[src]: raise ValueError()
                    item = dict(source=src, event=event,
                                language=str(d.get('language',''))[:20],
                                # The VS Code adapter is capped at 12 KB and only sends it
                                # when its own explicit workspace setting is enabled.
                                context=str(d.get('context',''))[:12288] if event == 'document_context' else '',
                                playing=bool(d.get('playing', False)), fullscreen=bool(d.get('fullscreen', False)))
                    try: bridge.events.put_nowait(item)
                    except queue.Full: pass
                    self._headers(200); self.wfile.write(b'{"ok":true}')
                except Exception:
                    self._headers(400); self.wfile.write(b'{"ok":false}')
        try:
            self.server = http.server.ThreadingHTTPServer(('127.0.0.1', self.PORT), Handler)
            self.server.daemon_threads = True
            self.thread = threading.Thread(target=self.server.serve_forever, name='chill-companion', daemon=True)
            self.thread.start(); return True
        except OSError:
            self.server = None; return False

    def drain(self):
        out = []
        while True:
            try: out.append(self.events.get_nowait())
            except queue.Empty: return out

    def request_like(self):
        """One manual, visible request; the extension alone performs the click."""
        if self.server is not None: self.like_requested = True

    def stop(self):
        if self.server is not None:
            self.server.shutdown(); self.server.server_close(); self.server = None


class DesktopIconMover(object):
    """Windows Explorer desktop *layout* mover; never touches file paths/data.

    Explorer owns desktop icon positions.  This class sends only LVM_SETITEMPOSITION
    to its desktop ListView. Windows Auto Arrange can override the layout, so the
    feature is deliberately opt-in and exposes a one-shot command first.
    """
    LVM_FIRST, LVM_GETITEMCOUNT, LVM_SETITEMPOSITION, LVM_GETITEMPOSITION = 0x1000, 0x1004, 0x100F, 0x1010
    def __init__(self):
        self.ok = False; self.u = None
        if os.name != 'nt': return
        try:
            import ctypes
            self.ct = ctypes; self.u = ctypes.windll.user32; self.ok = True
        except Exception: pass

    def _children(self, parent, klass):
        found = []
        if not self.ok: return found
        WNDENUMPROC = self.ct.WINFUNCTYPE(self.ct.c_bool, self.ct.c_void_p, self.ct.c_void_p)
        @WNDENUMPROC
        def cb(hwnd, _):
            buf = self.ct.create_unicode_buffer(128)
            self.u.GetClassNameW(hwnd, buf, 128)
            if buf.value == klass: found.append(hwnd)
            return True
        self.u.EnumChildWindows(parent, cb, 0); return found

    def _desktop_listview(self):
        if not self.ok: return None
        # Progman normally hosts it; some Windows shells host it below WorkerW.
        candidates = []
        prog = self.u.FindWindowW('Progman', None)
        if prog: candidates.append(prog)
        HWND = self.ct.c_void_p
        worker = HWND(0)
        while True:
            worker = self.u.FindWindowExW(0, worker, 'WorkerW', None)
            if not worker: break
            candidates.append(worker)
        for root in candidates:
            for view in self._children(root, 'SHELLDLL_DefView'):
                lvs = self._children(view, 'SysListView32')
                if lvs: return lvs[0]
        return None

    def count(self):
        hwnd = self._desktop_listview()
        return int(self.u.SendMessageW(hwnd, self.LVM_GETITEMCOUNT, 0, 0)) if hwnd else 0

    def _position(self, hwnd, index):
        """Read one icon's current position from Explorer's remote ListView."""
        try:
            k32 = self.ct.windll.kernel32
            pid = self.ct.c_ulong(); self.u.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
            proc = k32.OpenProcess(0x0038, False, pid.value)
            if not proc: return None
            class POINT(self.ct.Structure): _fields_ = [('x', self.ct.c_long), ('y', self.ct.c_long)]
            k32.VirtualAllocEx.restype = self.ct.c_void_p
            remote = k32.VirtualAllocEx(proc, None, self.ct.sizeof(POINT), 0x3000, 0x04)
            if not remote: k32.CloseHandle(proc); return None
            try:
                self.u.SendMessageW(hwnd, self.LVM_GETITEMPOSITION, index, remote)
                local = POINT(); read = self.ct.c_size_t()
                if not k32.ReadProcessMemory(proc, remote, self.ct.byref(local), self.ct.sizeof(local), self.ct.byref(read)):
                    return None
                return int(local.x), int(local.y)
            finally:
                k32.VirtualFreeEx(proc, remote, 0, 0x8000); k32.CloseHandle(proc)
        except Exception:
            return None

    def set_position(self, hwnd, index, x, y):
        packed = (int(x) & 0xffff) | ((int(y) & 0xffff) << 16)
        return bool(self.u.SendMessageW(hwnd, self.LVM_SETITEMPOSITION, index, packed))

    def plan_random(self, destination=None):
        hwnd = self._desktop_listview(); n = self.count() if hwnd else 0
        if not hwnd or n <= 0: return None, 'Desktop icons are unavailable.'
        i = random.randrange(n)
        start = self._position(hwnd, i)
        if start is None: return None, 'Windows did not expose the selected icon position.'
        # Wide, spaced grid points avoid piling icons directly over one another.
        x, y = destination or random.choice(((620,130),(780,130),(940,130),(620,300),(780,300),(940,300),(1120,220)))
        return dict(hwnd=hwnd, index=i, start=start, end=(x,y)), 'Ready to carry one desktop icon.'


# ----------------------------------------------------------------- reaction catalog
# Every action owns a pool of 2-4 variants.  A variant is real choreography:
#   dur   seconds
#   w     (sleepy, mischievous, affectionate) preference: how much each trait favours it
#   beats [(t, {...})]  one-shot kicks:  hop stretch ears ear_l ear_r head body lean tail shake
#                       spin=(turns, secs, dir) excite=(v, hold) happy lick aff blink_pulse fx=[kinds]
#   hold  eased-in targets while it plays:  head body lean ear ear_l ear_r tail stretch
#                       tongue=(target, weight) blink look=(x, y, weight)
#   osc   [(channel, amplitude, hz)] continuous wobble (head body lean tail ear stretch)
#   side  None = absolute directions, +1 toward the cursor, -1 away from it
#   sleep True = afterwards he actually curls up for a nap (only offered when napping is allowed)
# fx kinds:  b ! | q ? | a anger mark | d sweat drop | h heart | s sparkle | z zzz
REACTIONS = {
    'startled_hop': dict(feel=None, aff=-0.03, variants=[
        dict(id='tight', dur=0.95, w=(-0.2, 0.2, -0.1),
             beats=[(0.00, dict(hop=-300, stretch=9, ears=520, shake=0.5, fx=['b'], excite=(0.9, 1.0)))],
             hold=dict(ear=-12, head=-5, look=(0.0, -0.35, 0.8))),
        dict(id='double', dur=1.3, w=(-0.3, 0.5, 0.1),
             beats=[(0.00, dict(hop=-250, stretch=8, ears=480, shake=0.4, fx=['b'])),
                    (0.45, dict(hop=-170, stretch=5, ears=320, fx=['d']))],
             hold=dict(ear=-8), osc=[('head', 5.0, 6.5)]),
        dict(id='flinch', dur=1.15, w=(0.5, -0.4, 0.0), side=-1,
             beats=[(0.00, dict(hop=-110, stretch=-6, body=-110, head=-90, ears=-260, shake=0.35, fx=['d']))],
             hold=dict(body=-6, head=-8, ear=-16, tail=14, look=(0.7, 0.1, 0.8), blink=0.35)),
        dict(id='tumble', dur=1.4, w=(-0.6, 0.9, 0.1),
             beats=[(0.00, dict(hop=-290, stretch=8, ears=520, fx=['b'])),
                    (0.10, dict(spin=(1.0, 0.7, -1))),
                    (0.75, dict(hop=-90, stretch=-4, shake=0.3))],
             hold=dict(tail=-10, tongue=(1.1, 0.7))),
    ]),
    'annoyed_wiggle': dict(feel='mischievous', aff=-0.05, variants=[
        dict(id='huff', dur=1.25, w=(-0.2, 0.6, -0.5),
             beats=[(0.00, dict(head=-110, ears=-380, shake=0.7, fx=['a'], excite=(0.6, 1.0))),
                    (0.22, dict(head=140, tail=120, shake=0.6)),
                    (0.46, dict(head=-120, tail=-120, shake=0.5)),
                    (0.70, dict(head=100, shake=0.35))],
             hold=dict(ear=-14, tail=-16, tongue=(0.55, 0.8), blink=0.3, look=(0.0, 0.15, 0.5))),
        dict(id='sulk', dur=1.9, w=(0.6, -0.1, -0.4), side=-1,
             beats=[(0.00, dict(lean=-45, ears=-300, fx=['a']))],
             hold=dict(lean=-6, head=-6, ear=-20, tail=12, tongue=(0.42, 0.9), blink=0.55,
                       look=(0.8, 0.4, 0.9))),
        dict(id='stomp', dur=1.3, w=(-0.4, 0.8, -0.3),
             beats=[(0.00, dict(hop=-120, shake=0.5, fx=['a'], ears=-200)),
                    (0.30, dict(hop=-120, shake=0.5)),
                    (0.60, dict(hop=-140, shake=0.7, stretch=4))],
             hold=dict(ear=-10, tongue=(0.6, 0.7), look=(0.0, 0.2, 0.6))),
    ]),
    'playful_spin': dict(feel='mischievous', aff=0.03, variants=[
        dict(id='tight', dur=1.0, w=(-0.5, 0.2, 0.2),
             beats=[(0.00, dict(hop=-200, stretch=6, spin=(1.0, 0.5, 1), ears=380, fx=['s', 's'],
                                excite=(1.0, 1.2), happy=0.9))],
             hold=dict(tongue=(1.0, 0.8), ear=6)),
        dict(id='wobbly', dur=1.7, w=(-0.2, 0.9, 0.0),
             beats=[(0.00, dict(hop=-170, stretch=5, spin=(1.0, 1.05, 1), ears=300, shake=0.6, fx=['s'])),
                    (0.90, dict(hop=-120, shake=0.6, fx=['s']))],
             hold=dict(tongue=(1.2, 0.9)), osc=[('lean', 5.0, 2.6), ('head', 6.0, 3.1), ('tail', 10.0, 4.2)]),
        dict(id='double', dur=1.7, w=(-0.3, 0.4, 0.4),
             beats=[(0.00, dict(hop=-190, stretch=6, spin=(2.0, 1.15, 1), fx=['s', 's', 'h'], ears=360, happy=0.9)),
                    (1.20, dict(hop=-110, stretch=4))],
             hold=dict(tongue=(0.95, 0.6), ear=5)),
        dict(id='twirl_hop', dur=1.6, w=(0.0, 0.3, 0.3),
             beats=[(0.00, dict(hop=-150, spin=(1.0, 0.7, -1), ears=300, fx=['s'])),
                    (0.55, dict(hop=-150, stretch=5, ears=300, fx=['s'])),
                    (1.00, dict(hop=-110, stretch=3, shake=0.4))],
             hold=dict(tongue=(1.1, 0.7), tail=-8)),
    ]),
    'affectionate_nuzzle': dict(feel='affectionate', aff=0.12, variants=[
        dict(id='lean_in', dur=2.2, w=(0.1, -0.4, 0.8), side=1,
             beats=[(0.00, dict(lean=60, head=70, fx=['h'], happy=0.9, lick=True)),
                    (1.00, dict(head=-50, fx=['h']))],
             hold=dict(lean=5, head=5, blink=0.98, tongue=(0.9, 0.5), tail=-8)),
        dict(id='head_bump', dur=2.0, w=(-0.1, 0.1, 0.7), side=1,
             beats=[(0.00, dict(head=90, lean=40, fx=['h'])),
                    (0.45, dict(head=-70)), (0.90, dict(head=90, lean=30)),
                    (1.35, dict(head=-60, fx=['h']))],
             hold=dict(lean=3, ear=-6, blink=0.55, tongue=(0.85, 0.5))),
        dict(id='melt', dur=2.6, w=(0.8, -0.6, 0.6),
             beats=[(0.00, dict(stretch=-7, ears=-250)), (0.40, dict(fx=['h'])), (1.50, dict(fx=['h']))],
             hold=dict(stretch=-0.05, blink=0.98, tail=14, ear=-18, head=7), osc=[('head', 3.0, 0.8)]),
        dict(id='happy_wiggle', dur=1.6, w=(-0.6, 0.6, 0.5),
             beats=[(0.00, dict(shake=0.6, lick=True, tail=100, fx=['h', 's'], happy=0.9, excite=(0.9, 1.0))),
                    (0.70, dict(shake=0.5, fx=['h']))],
             hold=dict(tongue=(1.1, 0.8)), osc=[('body', 5.0, 4.0), ('tail', 12.0, 5.0)]),
    ]),
    'sleepy_settle': dict(feel='sleepy', aff=0.02, variants=[
        dict(id='yawn', dur=2.4, w=(0.7, -0.5, 0.1),
             beats=[(0.00, dict(stretch=-4, ears=-200, fx=['z'])), (1.20, dict(stretch=-6))],
             hold=dict(tongue=(1.5, 0.9), blink=0.98, head=-7, ear=-20)),
        dict(id='slump', dur=2.6, w=(0.9, -0.6, 0.2),
             beats=[(0.00, dict(stretch=-6, ears=-250)), (0.80, dict(fx=['z']))],
             hold=dict(head=10, ear=-26, blink=0.8, tail=14, tongue=(0.5, 0.8))),
        dict(id='curl_up', dur=2.8, w=(1.0, -0.7, 0.3), side=1, sleep=True,
             beats=[(0.00, dict(stretch=-7, fx=['z']))],
             hold=dict(lean=6, body=7, tail=20, blink=0.98, ear=-24, head=9)),
        dict(id='nod_off', dur=2.6, w=(0.8, -0.2, 0.0), sleep=True,
             beats=[(0.00, dict(head=60, stretch=-4)), (0.80, dict(head=70, fx=['z'])), (1.50, dict(head=80, stretch=-5))],
             hold=dict(blink=0.85, ear=-18, tail=10), osc=[('head', 5.0, 0.9)]),
    ]),
    'curious_perk': dict(feel=None, aff=0.02, variants=[
        dict(id='ears_up', dur=1.7, w=(-0.4, 0.2, 0.2), side=1,
             beats=[(0.00, dict(ears=480, fx=['q'], excite=(0.5, 0.8)))],
             hold=dict(ear=16, head=3, look=(0.9, -0.2, 0.9), tongue=(0.85, 0.5))),
        dict(id='head_tilt', dur=2.0, w=(0.0, 0.3, 0.3), side=1,
             beats=[(0.00, dict(head=80)), (0.30, dict(fx=['q']))],
             hold=dict(head=11, ear_l=10, ear_r=-6, look=(0.6, 0.0, 0.8))),
        dict(id='lean_peek', dur=1.8, w=(-0.2, 0.6, 0.2), side=1,
             beats=[(0.00, dict(lean=50, ears=300, fx=['q']))],
             hold=dict(lean=7, stretch=0.05, tongue=(1.0, 0.6), look=(1.0, 0.1, 0.9))),
    ]),
    'missed_you_stretch': dict(feel='affectionate', aff=0.10, variants=[
        dict(id='big_stretch', dur=2.4, w=(0.5, -0.2, 0.4),
             beats=[(0.00, dict(stretch=-5, ears=-200)), (0.50, dict(stretch=12)),
                    (1.40, dict(hop=-170, ears=420, fx=['h', 'h']))],
             hold=dict(tongue=(1.4, 0.9), ear=-18)),
        dict(id='trot_hop', dur=1.7, w=(-0.6, 0.5, 0.5),
             beats=[(0.00, dict(hop=-200, ears=400, happy=0.9, excite=(1.0, 1.6), fx=['h'])),
                    (0.35, dict(hop=-200, stretch=5)), (0.70, dict(hop=-230, stretch=6, fx=['s', 'h']))],
             hold=dict(tongue=(1.1, 0.7)), osc=[('tail', 12.0, 5.5)]),
        dict(id='shy_hello', dur=2.4, w=(0.2, -0.5, 0.7), side=1,
             beats=[(0.00, dict(head=60)), (0.60, dict(fx=['h'], aff=0.06))],
             hold=dict(head=8, ear=-10, tail=10, blink=0.55, tongue=(0.7, 0.6), look=(0.6, 0.4, 0.8))),
    ]),
    'redirected_blink': dict(feel=None, aff=0.0, variants=[
        dict(id='double_blink', dur=1.2, w=(0.2, 0.0, 0.0),
             beats=[(0.00, dict(blink_pulse=True, head=40)), (0.35, dict(blink_pulse=True))],
             hold=dict(look=(0.8, 0.0, 0.6))),
        dict(id='shrug', dur=1.0, w=(-0.2, 0.4, 0.0),
             beats=[(0.00, dict(hop=-90, stretch=3, ears=300, ear_l=-150))],
             hold=dict(tongue=(0.6, 0.6))),
        dict(id='nod', dur=1.1, w=(0.3, -0.2, 0.2),
             beats=[(0.00, dict(head=70)), (0.20, dict(blink_pulse=True)), (0.40, dict(head=70))],
             hold=dict(ear=4)),
    ]),
    'easter_egg_pirouette': dict(feel='mischievous', aff=0.04, variants=[
        dict(id='triple', dur=2.7, w=(-0.4, 0.8, 0.2),
             beats=[(0.00, dict(hop=-260, stretch=8, spin=(3.0, 1.9, 1), ears=450, fx=['s', 's', 's'],
                                excite=(1.0, 2.5), happy=0.9)),
                    (0.90, dict(fx=['s', 'h'], shake=0.4)),
                    (1.95, dict(hop=-200, stretch=6, fx=['s', 's']))],
             hold=dict(tongue=(1.2, 0.8)), osc=[('tail', 14.0, 5.0)]),
    ]),
    'easter_egg_confetti': dict(feel='mischievous', aff=0.04, variants=[
        dict(id='burst', dur=2.4, w=(-0.4, 0.8, 0.3),
             beats=[(0.00, dict(hop=-220, stretch=7, ears=450, shake=0.7, fx=['s', 's', 's', 'h'], happy=0.9,
                                excite=(1.0, 2.0))),
                    (0.35, dict(hop=-160, fx=['s', 's', 'h'], shake=0.5)),
                    (0.70, dict(hop=-160, fx=['s', 's', 's'], shake=0.5)),
                    (1.10, dict(hop=-190, stretch=6, fx=['s', 'h', 'h']))],
             hold=dict(tongue=(1.3, 0.9)), osc=[('body', 4.0, 3.5), ('tail', 14.0, 5.5)]),
    ]),
    'easter_egg_blep': dict(feel='mischievous', aff=0.03, variants=[
        dict(id='blep', dur=2.5, w=(0.0, 0.7, 0.3), side=1,
             beats=[(0.00, dict(stretch=6, ears=380, fx=['q', 's'])), (1.30, dict(hop=-120, fx=['s']))],
             hold=dict(tongue=(1.6, 1.0), head=8, look=(0.6, -0.1, 0.6)), osc=[('head', 5.0, 1.8), ('tail', 10.0, 4.0)]),
    ]),
}

# Extra hand-drawn choreography.  These are intentionally not cosmetic timing
# edits: each one changes the visible sequence (lean/crouch, hop count, spin,
# tail beat, expression and/or effect).  Keeping them as data means future
# actions get the same anti-repeat selection machinery for free.
_EXTRA_VARIANTS = {
    'startled_hop': [
        ('backstep', 1.25, [(0.00,dict(body=-130,head=-80,ears=-260,fx=['d'])),(.38,dict(hop=-190,stretch=5,fx=['b']))]),
        ('three_peep', 1.65, [(0.00,dict(hop=-145,ears=260,fx=['b'])),(.32,dict(hop=-135,tail=70)),(.68,dict(hop=-155,shake=.4))]),
        ('side_skitter', 1.35, [(0.00,dict(lean=-95,body=-90,ears=-190)),(.28,dict(hop=-125,stretch=4,fx=['d']))]),
        ('big_freeze', 1.55, [(0.00,dict(ears=590,head=-90,fx=['b'])),(.62,dict(hop=-180,stretch=6,shake=.45))]),
        ('tail_bottlebrush', 1.45, [(0.00,dict(hop=-205,tail=230,ears=390,fx=['b'])),(.55,dict(tail=-180,shake=.35))]),
        ('turn_away', 1.55, [(0.00,dict(hop=-170,spin=(.42,.45,-1),ears=360,fx=['d'])),(.70,dict(body=-80,tail=120))]),
        ('tiny_squeak', 1.10, [(0.00,dict(hop=-115,stretch=3,ears=440,fx=['q'])),(.30,dict(head=-80,blink_pulse=True))]),
    ],
    'annoyed_wiggle': [
        ('tail_slash',1.50,[(0.00,dict(tail=270,ears=-180,fx=['a'])),(.26,dict(tail=-260,head=100)),(.52,dict(tail=210,head=-95))]),
        ('turn_huff',1.65,[(0.00,dict(spin=(.30,.48,-1),ears=-260,fx=['a'])),(.58,dict(body=-100,tail=160))]),
        ('four_stomp',1.65,[(0.00,dict(hop=-100,fx=['a'])),(.24,dict(hop=-100)),(.48,dict(hop=-100)),(.72,dict(hop=-125,shake=.6))]),
        ('side_eye',1.55,[(0.00,dict(head=-120,ear_l=-300,tail=130)),(.50,dict(fx=['a'],blink_pulse=True))]),
        ('shake_off',1.45,[(0.00,dict(shake=.9,ears=-260,fx=['d'])),(.36,dict(tail=170)),(.70,dict(head=110))]),
        ('sulk_hop',1.80,[(0.00,dict(body=-110,ears=-280)),(.70,dict(hop=-140,stretch=4,fx=['a']))]),
        ('nope_spin',1.50,[(0.00,dict(spin=(.55,.65,1),tail=150,fx=['a'])),(.82,dict(head=-90,ears=-180))]),
    ],
    'playful_spin': [
        ('windup_twirl',1.85,[(0.00,dict(lean=-120,tail=-170,ears=190)),(.30,dict(spin=(1.15,.85,1),hop=-160,fx=['s'])),(1.15,dict(hop=-110,fx=['s']))]),
        ('pinwheel',1.55,[(0.00,dict(spin=(1.65,1.10,-1),stretch=7,ears=430,fx=['s','s']))]),
        ('two_step_turn',1.75,[(0.00,dict(hop=-125,tail=120)),(.35,dict(spin=(.75,.55,1),fx=['s'])),(.95,dict(hop=-145,spin=(.60,.42,-1),fx=['h']))]),
        ('tail_propeller',1.50,[(0.00,dict(spin=(.85,.72,1),tail=270,fx=['s'])),(.75,dict(tail=-240,shake=.45))]),
        ('low_twirl',1.95,[(0.00,dict(stretch=-6,ears=-130)),(.38,dict(spin=(1.10,1.10,1),fx=['s'])),(1.35,dict(hop=-120,stretch=4))]),
        ('bounce_turn',1.85,[(0.00,dict(hop=-170,spin=(.45,.42,1))),(.48,dict(hop=-190,spin=(.72,.58,1),fx=['s'])),(1.15,dict(hop=-120,fx=['h']))]),
        ('dizzy_recover',2.10,[(0.00,dict(spin=(1.35,1.00,-1),ears=380,fx=['s'])),(1.12,dict(head=150,shake=.55)),(1.62,dict(blink_pulse=True))]),
    ],
    'affectionate_nuzzle': [
        ('shy_peek',2.25,[(0.00,dict(head=65,ear_l=-130,fx=['h'])),(.72,dict(lean=65,blink_pulse=True)),(1.38,dict(head=-45,fx=['h']))]),
        ('double_bump',2.05,[(0.00,dict(lean=65,head=115,fx=['h'])),(.42,dict(head=-90)),(.92,dict(head=105,tail=100)),(1.42,dict(head=-85,fx=['h']))]),
        ('heart_hop',1.90,[(0.00,dict(hop=-125,stretch=4,fx=['h'])),(.52,dict(lean=65,head=70,fx=['h'])),(1.10,dict(hop=-95))]),
        ('purr_wiggle',2.30,[(0.00,dict(tail=150,shake=.35,lick=True,fx=['h'])),(.55,dict(tail=-130)),(1.12,dict(tail=140,fx=['h']))]),
        ('cheek_rub',2.20,[(0.00,dict(head=125,lean=75,ears=-120,fx=['h'])),(.65,dict(head=-100)),(1.20,dict(head=95,blink_pulse=True))]),
        ('roll_in',2.05,[(0.00,dict(spin=(.28,.45,1),hop=-95,fx=['h'])),(.55,dict(lean=80,head=80)),(1.22,dict(tail=110,fx=['h']))]),
        ('soft_bow',2.35,[(0.00,dict(stretch=-7,ears=-200)),(.52,dict(lean=60,head=75,fx=['h'])),(1.45,dict(stretch=7,fx=['h']))]),
    ],
    'sleepy_settle': [
        ('slow_yawn',2.90,[(0.00,dict(stretch=-6,ears=-220,fx=['z'])),(.70,dict(head=80)),(1.55,dict(stretch=-5,fx=['z']))]),
        ('tail_wrap',2.70,[(0.00,dict(tail=210,stretch=-5,ears=-210)),(.90,dict(tail=120,fx=['z']))]),
        ('three_nods',2.55,[(0.00,dict(head=70)),(.48,dict(head=90)),(.98,dict(head=105,fx=['z']))]),
        ('tiny_curl',2.80,[(0.00,dict(lean=60,body=70,tail=180,fx=['z'])),(1.05,dict(ears=-190,stretch=-5))]),
        ('sleepy_blink',2.30,[(0.00,dict(blink_pulse=True,ears=-160)),(.45,dict(blink_pulse=True)),(.95,dict(blink_pulse=True,fx=['z']))]),
        ('stretch_then_flop',2.85,[(0.00,dict(stretch=10,ears=120)),(.58,dict(stretch=-8,head=90,fx=['z']))]),
        ('dreamy_sway',3.00,[(0.00,dict(tail=120,head=55,fx=['z'])),(.78,dict(tail=-130,head=-45)),(1.52,dict(tail=110,fx=['z']))]),
    ],
    'curious_perk': [
        ('double_tilt',1.95,[(0.00,dict(head=105,ears=350,fx=['q'])),(.52,dict(head=-125,ear_l=170)),(1.05,dict(head=105,fx=['q']))]),
        ('tiptoe_peek',1.80,[(0.00,dict(hop=-90,stretch=3,ears=390)),(.35,dict(lean=85,head=75,fx=['q']))]),
        ('tail_question',1.70,[(0.00,dict(tail=190,ears=430,fx=['q'])),(.55,dict(tail=-150,head=70))]),
        ('little_gasp',1.45,[(0.00,dict(hop=-120,ears=520,fx=['q'])),(.42,dict(blink_pulse=True,head=-65))]),
        ('lean_round',1.95,[(0.00,dict(lean=100,head=80,fx=['q'])),(.63,dict(lean=-75,tail=95)),(1.15,dict(lean=60))]),
        ('ear_radar',1.80,[(0.00,dict(ear_l=430,ear_r=-310,fx=['q'])),(.38,dict(ear_l=-300,ear_r=420)),(.78,dict(ears=250))]),
        ('peek_hop',1.65,[(0.00,dict(hop=-120,lean=65,fx=['q'])),(.52,dict(hop=-95,head=75))]),
    ],
}

def _expand_reaction_variants():
    """Guarantee a visibly distinct pool of at least ten scripts per core action."""
    for action, recipes in _EXTRA_VARIANTS.items():
        variants = REACTIONS[action]['variants']
        for ident, dur, beats in recipes:
            variants.append(dict(id=ident, dur=dur, w=(0.0, 0.0, 0.0), beats=beats,
                                 hold=dict(tongue=(0.78, 0.42))))
        # The supplied catalog already has 3-4 base takes plus seven specific
        # takes above.  This assertion catches future edits that reduce variety.
        assert len(variants) >= 10, '%s needs ten takes' % action
    # The rarer families use their own base choreography plus these broad,
    # visibly different staged takes. They remain within the family’s emotion
    # (e.g. a blink stays a blink; an easter egg stays celebratory).
    staged = [
        ('anticipate', [(0.00, dict(lean=-65, tail=-95)), (.26, dict(hop=-115, stretch=4, fx=['s']))]),
        ('double_beat', [(0.00, dict(hop=-95, ears=220)), (.34, dict(hop=-120, tail=105, fx=['s']))]),
        ('side_sway', [(0.00, dict(lean=95, head=70)), (.45, dict(lean=-105, head=-80, fx=['s']))]),
        ('tail_dance', [(0.00, dict(tail=190, fx=['s'])), (.36, dict(tail=-205)), (.72, dict(tail=145, fx=['h']))]),
        ('tiny_turn', [(0.00, dict(spin=(.32,.45,1), ears=220)), (.56, dict(hop=-105, fx=['s']))]),
        ('bounce_bow', [(0.00, dict(stretch=-5, ears=-110)), (.36, dict(hop=-150, stretch=6, fx=['s']))]),
        ('shy_finish', [(0.00, dict(head=80, ear_l=-145)), (.48, dict(blink_pulse=True, tail=100, fx=['h']))]),
        ('zigzag', [(0.00, dict(lean=110, hop=-100, fx=['s'])), (.32, dict(lean=-135, hop=-125)), (.70, dict(lean=85, fx=['h']))]),
        ('big_finish', [(0.00, dict(hop=-185, stretch=7, ears=360, fx=['s','s'])), (.62, dict(tail=180, shake=.45, fx=['h']))]),
    ]
    for action, spec in REACTIONS.items():
        variants = spec['variants']
        for ident, beats in staged:
            if len(variants) >= 10:
                break
            variants.append(dict(id='stage_' + ident, dur=1.55 + .08 * len(variants),
                                 w=(0.0, 0.0, 0.0), beats=beats,
                                 hold=dict(tongue=(0.74, 0.34))))
        assert len(variants) >= 10, '%s needs ten takes' % action

_expand_reaction_variants()

# which actions Brain is willing to offer for each trigger (the model can only pick from these)
TRIGGER_MENUS = {
    'click':            ['curious_perk', 'startled_hop', 'annoyed_wiggle', 'affectionate_nuzzle'],
    'double':           ['playful_spin', 'affectionate_nuzzle', 'startled_hop', 'annoyed_wiggle'],
    'rapid':            ['annoyed_wiggle', 'playful_spin', 'startled_hop', 'affectionate_nuzzle'],
    'drag':             ['affectionate_nuzzle', 'curious_perk', 'sleepy_settle', 'playful_spin'],
    'throw':            ['startled_hop', 'playful_spin', 'annoyed_wiggle'],
    'long_idle_return': ['missed_you_stretch', 'affectionate_nuzzle', 'curious_perk'],
    'perch_lost':       ['startled_hop', 'annoyed_wiggle', 'curious_perk'],
    'poke_asleep':      ['startled_hop', 'annoyed_wiggle', 'sleepy_settle', 'affectionate_nuzzle'],
    'mode_chill':       ['redirected_blink', 'playful_spin', 'curious_perk'],
    'mode_follow':      ['redirected_blink', 'playful_spin', 'curious_perk'],
    'mode_still':       ['redirected_blink', 'sleepy_settle', 'curious_perk'],
    'easter_egg':       ['easter_egg_pirouette', 'easter_egg_confetti', 'easter_egg_blep'],
    'first_of_day':     ['missed_you_stretch', 'curious_perk', 'affectionate_nuzzle', 'sleepy_settle'],
}
URGENT_TRIGGERS = ('poke_asleep', 'perch_lost')

def burst_menu_key(counts):
    """which menu fits what happened during the burst"""
    total = sum(counts.values())
    if counts.get('triple') or counts.get('click', 0) >= 3 or total >= 4:
        return 'rapid'
    if counts.get('throw'):
        return 'throw'
    if counts.get('drag'):
        return 'drag'
    if counts.get('double'):
        return 'double'
    return 'click'

def pick_variant(action, bias, rng, last_id=None, allow=None):
    """weighted by disposition; never the same variant twice in a row if avoidable"""
    vs = REACTIONS[action]['variants']
    if allow is not None:
        ok = [v for v in vs if allow(v)]
        vs = ok or [v for v in vs if not v.get('sleep')] or vs
    ws = []
    for v in vs:
        sw, mw, aw = v['w']
        tilt = sw * bias['sleepy'] + mw * bias['mischievous'] + aw * bias['affectionate']
        wgt = max(0.15, 1.0 + 0.9 * tilt)
        if v['id'] == last_id and len(vs) > 1:
            wgt *= 0.4
        ws.append(wgt)
    return rng.choices(vs, weights=ws, k=1)[0]


# ----------------------------------------------------------------- grammar + prompt
# Speech is intentionally selected from a small contextual phrase bank.  The tiny
# model is good at choosing among choices; free prose caused occasional nonsense.
# This keeps every visible bubble grounded, short, and character-consistent.
INTENTS = ('watch_cursor', 'rest_nearby', 'ask_attention', 'play', 'settle')
ACTION_INTENTS = {
    'startled_hop': ('watch_cursor', 'rest_nearby'), 'annoyed_wiggle': ('ask_attention', 'rest_nearby'),
    'playful_spin': ('play', 'ask_attention'), 'affectionate_nuzzle': ('rest_nearby', 'ask_attention'),
    'sleepy_settle': ('settle', 'rest_nearby'), 'curious_perk': ('watch_cursor', 'ask_attention'),
    'missed_you_stretch': ('rest_nearby', 'play'), 'redirected_blink': ('watch_cursor', 'settle'),
    'easter_egg_pirouette': ('play',), 'easter_egg_confetti': ('play',), 'easter_egg_blep': ('watch_cursor', 'play'),
}
ACTION_LINES = {
    'startled_hop': ('whoa, easy there.', 'that surprised me.'),
    'annoyed_wiggle': ('hey, gentle paws please.', 'i need a tiny break.'),
    'playful_spin': ('watch this!', 'i feel bouncy today.'),
    'affectionate_nuzzle': ('i like being near you.', 'this feels nice.'),
    'sleepy_settle': ('i am getting cozy.', 'just resting my eyes.'),
    'curious_perk': ('what is that?', 'i am watching you.'),
    'missed_you_stretch': ('you are back!', 'i missed this spot.'),
    'redirected_blink': ('okay, new plan.', 'i can do that.'),
    'easter_egg_pirouette': ('surprise spin!',), 'easter_egg_confetti': ('tiny party time!',),
    'easter_egg_blep': ('blep.',),
}

def _alts(values):
    return ' | '.join('"%s"' % v.replace('"', '') for v in values)

def allowed_intents(menu):
    return tuple(sorted(set(i for a in menu for i in ACTION_INTENTS.get(a, INTENTS))))

def lines_for_menu(menu):
    return tuple(line for a in menu for line in ACTION_LINES.get(a, ('i am here.',)))

def build_grammar(menu, intents=None, lines=None):
    intents = intents or allowed_intents(menu)
    lines = lines or lines_for_menu(menu)
    return ('root ::= action "\\\",\\\"intent\\\":\\\"" intent "\\\",\\\"line\\\":\\\"" line "\\\"}"\n'
            'action ::= %s\nintent ::= %s\nline ::= %s\n') % (_alts(menu), _alts(intents), _alts(lines))

PROMPT_PREFIX = '{"action":"'
SYSTEM_PROMPT = ('You are Chill, a gentle desktop pet. Pick only from the exact action, intent, and line choices. '
                 'Do not invent facts, names, places, or new words. Return the constrained JSON completion only.')
TRIGGER_TEXT = {'long_idle_return':'the person just came back after being away for {away}', 'perch_lost':'your perch vanished',
                'poke_asleep':'you were poked while napping', 'easter_egg':'a rare silly mood appeared',
                'first_of_day':'this is the first touch today'}
MODE_TEXT = {'chill':'wander freely', 'follow':'follow the cursor', 'still':'stay put'}
MOOD_TEXT = {'music':'music nearby', 'video':'a video nearby', 'work':'a work app nearby', 'game':'a game nearby', None:'the desktop'}
GESTURE_TEXT = {'click':'poke','double':'double-click','triple':'triple-click','drag':'gentle pick-up','throw':'fast throw'}

def human_secs(s):
    if s is None: return 'unknown'
    s=float(s)
    if s < 90: return '%d seconds' % round(s)
    if s < 5400: return '%d minutes' % round(s/60.0)
    return '%.1f hours' % (s/3600.0)

def describe_event(ctx):
    t=ctx['trigger']
    if t == 'gesture_burst' or ctx.get('via') == 'gesture_burst':
        parts=['%dx %s' % (n,GESTURE_TEXT.get(k,k)) for k,n in ctx.get('burst',{}).items() if n]
        return 'the person touched you: ' + (', '.join(parts) or 'a poke')
    if t == 'mode_switch': return 'the person told you to %s' % MODE_TEXT.get(ctx.get('mode'),'change behaviour')
    return TRIGGER_TEXT.get(t,t).format(away=human_secs(ctx.get('away')))

def build_prompt(ctx, menu, intents, lines):
    mem=ctx.get('memory') or {}
    rows=['event: '+describe_event(ctx), 'state: '+('napping' if ctx.get('sleeping') else 'awake'),
          'screen: '+MOOD_TEXT.get(ctx.get('mood'),MOOD_TEXT[None]), 'touches today: %d' % ctx.get('count_today',0),
          'disposition: '+(ctx.get('disp_word') or 'balanced'),
          'memory: comfort %(comfort)s, wary %(wary)s, familiar places %(places)s' % mem,
          'actions: '+', '.join(menu), 'intents: '+', '.join(intents), 'lines: '+' | '.join(lines)]
    return '<|im_start|>system\n%s<|im_end|>\n<|im_start|>user\n%s<|im_end|>\n<|im_start|>assistant\n%s' % (SYSTEM_PROMPT,'\n'.join(rows),PROMPT_PREFIX)

def parse_reply(text, menu, intents=None, lines=None):
    intents=intents or allowed_intents(menu); lines=lines or lines_for_menu(menu)
    try:
        d=json.loads(PROMPT_PREFIX+(text or ''))
        a,i,line=d.get('action'),d.get('intent'),d.get('line')
        if a in menu and i in intents and line in lines:
            return a,i,line
    except Exception:
        pass
    return None

# ----------------------------------------------------------------- thought engine
import gc, importlib, importlib.util, subprocess, urllib.request

class Unavailable(Exception):
    """the LLM layer cannot run here (no runtime, no model, no CUDA, no VRAM...)"""

class ThoughtEngine(object):
    """Own thread + queue.  Brain only ever calls submit() and poll(), both non-blocking.
    The model is loaded lazily on the first request and unloaded again after idle_unload seconds."""
    MAX_TOKENS = 32

    def __init__(self, backend_factory, idle_unload=720.0, debug=False):
        self._factory = backend_factory
        self.idle_unload = idle_unload
        self.debug = debug
        self._cv = threading.Condition()
        self._pending = None
        self._results = queue.Queue()
        self._backend = None
        self._stop = False
        self._thread = None
        self._last_use = time.monotonic()
        self._errors = 0
        self.poll_interval = 5.0
        self.status = 'idle'        # idle | loading | ready | generating | unavailable
        self.reason = ''
        self.busy = False
        self.loaded = False
        self.stats = dict(loads=0, unloads=0, generations=0)

    # ---- main-thread API
    @property
    def usable(self):
        return self.status != 'unavailable' and not self._stop

    def start(self):
        if self._thread is None:
            self._stop = False
            self._thread = threading.Thread(target=self._run, name='chill-thought', daemon=True)
            self._thread.start()

    def stop(self, wait=2.0):
        with self._cv:
            self._stop = True
            self._pending = None
            self._cv.notify_all()
        t = self._thread
        if t is not None and wait:
            t.join(wait)

    def submit(self, req):
        if not self.usable:
            return False
        self.start()
        with self._cv:
            self._pending = req        # a newer request replaces an older one that never started
            self.busy = True
            self._cv.notify_all()
        return True

    def poll(self):
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None

    def reset_unavailable(self):
        if self.status == 'unavailable':
            self.status = 'idle'
            self.reason = ''
            self._errors = 0

    # ---- worker
    def _log(self, msg):
        if self.debug:
            print('[chill-pet] ' + msg, flush=True)

    def _run(self):
        while True:
            with self._cv:
                if self._pending is None and not self._stop:
                    self._cv.wait(timeout=self.poll_interval)
                if self._stop:
                    break
                req, self._pending = self._pending, None
            if req is None:
                self._maybe_unload()
                continue
            self._results.put(self._handle(req))
            self.busy = self._pending is not None
        self._unload()

    def _handle(self, req):
        res = dict(id=req['id'], ok=False, action=None, line=None, reason='')
        t0 = time.monotonic()
        try:
            if self._backend is None:
                self.status = 'loading'
                be = self._factory()
                be.load()
                self._backend = be
                self.loaded = True
                self.stats['loads'] += 1
                self._log('model loaded in %.2fs' % (time.monotonic() - t0))
            if time.monotonic() > req['deadline']:
                res['reason'] = 'stale'
                self.status = 'ready'
                return res
            self.status = 'generating'
            text, finish = self._backend.generate(req['prompt'], req['grammar'], req['deadline'], self.MAX_TOKENS)
            self.stats['generations'] += 1
            if finish == 'timeout':
                res['reason'] = 'timeout'
            else:
                parsed = parse_reply(text, req['menu'], req.get('intents'), req.get('lines'))
                if parsed is None:
                    res['reason'] = 'invalid'
                else:
                    res.update(ok=True, action=parsed[0], intent=parsed[1], line=parsed[2], reason=finish or '')
            self._errors = 0
            self.status = 'ready'
            self._log('%s -> %r in %.2fs (%s)' % (req['menu'], (parsed if finish != 'timeout' else None), time.monotonic() - t0, finish))
        except Unavailable as e:
            self._mark_unavailable(str(e))
            res['reason'] = 'unavailable'
        except Exception as e:            # CUDA / VRAM problems, runtime hiccups...
            self._errors += 1
            res['reason'] = 'error'
            self._log('generation error: %r' % (e,))
            self._unload()
            if self._errors >= 3:
                self._mark_unavailable('repeated errors: %s' % (e,))
                res['reason'] = 'unavailable'
            else:
                self.status = 'idle'
        self._last_use = time.monotonic()
        return res

    def _mark_unavailable(self, why):
        self.status = 'unavailable'
        self.reason = why
        self._unload()
        self._log('LLM layer unavailable: ' + why)

    def _maybe_unload(self):
        if self._backend is not None and time.monotonic() - self._last_use > self.idle_unload:
            self._log('idle - unloading model from VRAM')
            self._unload()
            self.status = 'idle'

    def _unload(self):
        be, self._backend = self._backend, None
        self.loaded = False
        if be is not None:
            self.stats['unloads'] += 1
            try:
                be.unload()
            except Exception:
                pass
        gc.collect()


# ----------------------------------------------------------------- llama.cpp backend
MODEL_DIR = os.path.join(os.path.expanduser('~'), '.chill_pet', 'models')
PREFERRED_MODEL_FILE = 'Qwen3.5-0.8B.Q4_K_M.gguf'
LEGACY_MODEL_FILE = 'Qwen3-0.6B.Q4_K_M.gguf'
FALLBACK_MODEL_FILE = 'qwen2.5-0.5b-instruct-q4_k_m.gguf'
MODEL_FILE = PREFERRED_MODEL_FILE
MODEL_URL = ('https://huggingface.co/mradermacher/Qwen3.5-0.8B-GGUF/resolve/main/' + PREFERRED_MODEL_FILE)
MODEL_MIN_BYTES = 300 * 1024 * 1024
CUDA_INDEXES = ('https://abetlen.github.io/llama-cpp-python/whl/cu124',
                'https://abetlen.github.io/llama-cpp-python/whl/cu123',
                'https://abetlen.github.io/llama-cpp-python/whl/cu122',
                'https://abetlen.github.io/llama-cpp-python/whl/cu121')

def default_model_path():
    # Upgrade automatically when a newer Qwen brain is present, but keep the
    # already downloaded older/faster brains as zero-effort local fallbacks.
    for name in (PREFERRED_MODEL_FILE, LEGACY_MODEL_FILE, FALLBACK_MODEL_FILE):
        path = os.path.join(MODEL_DIR, name)
        try:
            if os.path.getsize(path) >= MODEL_MIN_BYTES:
                return path
        except OSError:
            pass
    return os.path.join(MODEL_DIR, PREFERRED_MODEL_FILE)

def model_label(path):
    name = os.path.basename(path or '')
    if name.lower().startswith('qwen3.5-0.8b'):
        return 'Qwen3.5 0.8B'
    if name.lower().startswith('qwen3-0.6b'):
        return 'Qwen3 0.6B'
    if name.lower().startswith('qwen2.5-0.5b'):
        return 'Qwen2.5 0.5B fallback'
    return name or 'local model'

def cuda_dll_dirs():
    """folders that may hold cudart / cublas: CUDA_PATH and the nvidia-* pip packages"""
    out = []
    cp = os.environ.get('CUDA_PATH')
    if cp and os.path.isdir(os.path.join(cp, 'bin')):
        out.append(os.path.join(cp, 'bin'))
    try:
        import site
        roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    except Exception:
        roots = []
    for r in roots:
        nv = os.path.join(r, 'nvidia')
        if os.path.isdir(nv):
            for sub in sorted(os.listdir(nv)):
                for leaf in ('bin', 'lib'):
                    p = os.path.join(nv, sub, leaf)
                    if os.path.isdir(p):
                        out.append(p)
    return out

def add_cuda_dll_dirs():
    for p in cuda_dll_dirs():
        try:
            if hasattr(os, 'add_dll_directory') and sys.platform == 'win32':
                os.add_dll_directory(p)
            if p not in os.environ.get('PATH', ''):
                os.environ['PATH'] = p + os.pathsep + os.environ.get('PATH', '')
        except Exception:
            pass

class LlamaBackend(object):
    """llama-cpp-python, fully offloaded to the GPU.  Nothing here touches the network."""
    def __init__(self, model_path):
        self.model_path = model_path
        self.llm = None
        self._lc = None
        self._grammars = {}

    def load(self):
        add_cuda_dll_dirs()
        try:
            import llama_cpp
        except Exception as e:
            raise Unavailable('llama-cpp-python not usable (%s)' % (e,))
        if not os.path.exists(self.model_path):
            raise Unavailable('model file missing: %s' % self.model_path)
        try:
            gpu = bool(llama_cpp.llama_supports_gpu_offload())
        except Exception:
            gpu = False
        if not gpu and not os.environ.get('CHILL_PET_ALLOW_CPU'):
            raise Unavailable('this llama.cpp build has no GPU offload (set CHILL_PET_ALLOW_CPU=1 to allow CPU)')
        try:
            self.llm = llama_cpp.Llama(model_path=self.model_path, n_gpu_layers=(-1 if gpu else 0),
                                       n_ctx=512, n_batch=256, n_threads=2, n_threads_batch=2,
                                       verbose=False)
        except Exception as e:
            raise Unavailable('could not load the model (%s)' % (e,))
        self._lc = llama_cpp

    def _grammar(self, text):
        g = self._grammars.get(text)
        if g is None:
            if len(self._grammars) > 48:
                self._grammars.clear()
            g = self._lc.LlamaGrammar.from_string(text, verbose=False)
            self._grammars[text] = g
        return g

    def generate(self, prompt, grammar_text, deadline, max_tokens):
        """Stream one token at a time so the worker releases llama.cpp at deadline.
        UI fallback is still independent; this prevents a late worker reply from
        continuing a whole completion after the reaction has already timed out.
        """
        lc = self._lc
        kw = {}
        scl = getattr(lc, 'StoppingCriteriaList', None)
        if scl is not None:
            kw['stopping_criteria'] = scl([lambda ids, logits: time.monotonic() >= deadline])
        stream = self.llm.create_completion(
            prompt, max_tokens=max_tokens, temperature=0.85, top_p=0.92,
            top_k=40, repeat_penalty=1.08, grammar=self._grammar(grammar_text),
            stop=['<|im_end|>'], stream=True, **kw)
        pieces, finish = [], None
        try:
            for chunk in stream:
                if time.monotonic() >= deadline:
                    finish = 'timeout'
                    break
                choice = (chunk.get('choices') or [{}])[0]
                pieces.append(choice.get('text') or '')
                finish = choice.get('finish_reason') or finish
        finally:
            close = getattr(stream, 'close', None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
        return ''.join(pieces), finish or ('timeout' if time.monotonic() >= deadline else 'stop')

    def unload(self):
        llm, self.llm = self.llm, None
        self._grammars.clear()
        try:
            if llm is not None:
                llm.close()
        except Exception:
            pass
        del llm
        gc.collect()


# ----------------------------------------------------------------- first-use setup
def runtime_present():
    try:
        return importlib.util.find_spec('llama_cpp') is not None
    except Exception:
        return False

def needs_setup(model_path):
    return not (runtime_present() and os.path.exists(model_path) and os.path.getsize(model_path) >= MODEL_MIN_BYTES)

def _pip(args):
    kw = {}
    if sys.platform == 'win32':
        kw['creationflags'] = 0x08000000          # no console flash
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check'] + args, **kw)

def _gpu_runtime_ok():
    """checked in a child process so a broken DLL load can never take the pet down"""
    env = dict(os.environ)
    dirs = cuda_dll_dirs()
    if dirs:
        env['PATH'] = os.pathsep.join(dirs) + os.pathsep + env.get('PATH', '')
    code = ('import os,sys\n'
            'for p in %r:\n'
            '    try: os.add_dll_directory(p)\n'
            '    except Exception: pass\n'
            'import llama_cpp\n'
            'sys.exit(0 if llama_cpp.llama_supports_gpu_offload() else 3)\n' % (dirs,))
    try:
        return subprocess.call([sys.executable, '-c', code], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=120) == 0
    except Exception:
        return False

def install_runtime(status):
    """pip-install a prebuilt CUDA llama-cpp-python (+ the CUDA runtime DLL packages).  Raises Unavailable."""
    if runtime_present() and _gpu_runtime_ok():
        return
    status('installing his GPU runtime...')
    try:
        _pip(['nvidia-cuda-runtime-cu12', 'nvidia-cublas-cu12'])        # cudart / cublas DLLs, best effort
    except Exception:
        pass
    last = None
    for idx in CUDA_INDEXES:
        try:
            status('installing his GPU runtime (%s)...' % idx.rsplit('/', 1)[-1])
            _pip(['llama-cpp-python', '--extra-index-url', idx, '--prefer-binary', '--only-binary=llama-cpp-python',
                  '--force-reinstall', '--no-deps'])
            _pip(['numpy', 'typing-extensions', 'diskcache', 'jinja2'])  # the deps we skipped with --no-deps
            importlib.invalidate_caches()
            if _gpu_runtime_ok():
                return
            last = 'installed, but it cannot offload to the GPU'
        except Exception as e:
            last = str(e)
    raise Unavailable('could not install a CUDA build of llama-cpp-python (%s)' % last)

def download_model(url, dest, status):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + '.part'
    have = os.path.getsize(part) if os.path.exists(part) else 0
    hdr = {'User-Agent': 'chill-pet'}
    if have:
        hdr['Range'] = 'bytes=%d-' % have
    status('downloading his brain... 0%')
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30) as r:
        resumed = (r.getcode() == 206)
        if have and not resumed:
            have = 0
        total = have + int(r.headers.get('Content-Length') or 0)
        done, last = have, 0.0
        with open(part, 'ab' if resumed else 'wb') as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if total and now - last > 0.4:
                    last = now
                    status('downloading his brain... %d%%' % min(99, int(done * 100.0 / total)))
    if os.path.getsize(part) < MODEL_MIN_BYTES:
        os.remove(part)
        raise Unavailable('model download looks incomplete')
    os.replace(part, dest)

class BrainSetup(threading.Thread):
    """Background readiness check only; it never installs or downloads anything."""
    def __init__(self, model_path, url=MODEL_URL):
        super(BrainSetup, self).__init__(name='chill-setup', daemon=True)
        self.model_path, self.url = model_path, url
        self.msg = 'getting his brain ready...'
        self.done = False
        self.ok = False
        self.error = ''

    def _status(self, m):
        self.msg = m

    def run(self):
        try:
            self._status('checking %s...' % model_label(self.model_path))
            if not runtime_present():
                raise Unavailable('GPU runtime is not installed; see README.md')
            if not _gpu_runtime_ok():
                raise Unavailable('GPU offload is unavailable; see README.md')
            if not (os.path.exists(self.model_path) and os.path.getsize(self.model_path) >= MODEL_MIN_BYTES):
                raise Unavailable('local GGUF model is missing; see README.md')
            self.ok = True
        except Exception as e:
            self.error = str(e)
            self.ok = False
        self.done = True


# ----------------------------------------------------------------- reactor (the Brain-side glue)
class Reactor(object):
    """Turns interaction triggers into (at most) one generation request each, and plays the
    reaction that comes back.  Never blocks: results are polled once per frame."""
    BURST_QUIET = 0.9        # a burst ends after this long without a new gesture...
    BURST_MAX = 3.2          # ...or when it has lasted this long
    WAIT_WARM = 1.6          # how long a reply may take before we fall back to a random pick
    WAIT_COLD = 5.0          # same, while the model is still being loaded into VRAM
    LONG_IDLE_S = 900.0      # cursor untouched this long -> "missed you" when it moves again
    LONG_ABSENCE_S = 7200.0  # pet last touched this long ago at launch -> "missed you" too

    def __init__(self, brain, state=None, engine=None, rng=None):
        self.b = brain
        self.state = state
        self.engine = engine
        self.rng = rng or random
        self.enabled = False
        self.clock = 0.0
        self.gestures = []           # [(t, kind)] of the burst in progress
        self.burst_start = None
        self.burst_since = None
        self.pending = None
        self.cur = None
        self.queued = None
        self.req_id = 0
        self.last_variant = {}
        self.last_action = None
        self.startup_due = None
        self.stats = dict(llm=0, fallback=0, dropped=0, fired=0)

    # ---- switches
    @property
    def active(self):
        return bool(self.enabled and self.state is not None and self.engine is not None and self.engine.usable)

    def set_enabled(self, on):
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        self.gestures, self.burst_start, self.pending = [], None, None
        self.cur = self.queued = None
        if on:
            ls = self.state.since_last() if self.state is not None else None
            if ls is not None and ls > self.LONG_ABSENCE_S:
                self.startup_due = self.clock + 3.0
        else:
            self.startup_due = None
            if self.state is not None:
                self.state.save(force=True)

    def target_bias(self):
        if self.active:
            return disp_biases(self.state.disp)
        return dict((k, 0.0) for k in DISP_KEYS)

    # ---- inputs
    def _touch(self, kind=None):
        if self.state is not None:
            self.state.touch()
            rough = kind in ('throw', 'poke_asleep', 'rapid')
            gentle = kind in ('click', 'double', 'drag', 'long_idle_return')
            self.state.observe_world(self.b.mood or 'desktop', gentle=gentle, rough=rough)

    def gesture(self, kind):
        """click / double / drag / throw - collected into one burst"""
        if not self.enabled:
            if self.state is not None:
                self.state.record_background_interaction()
                self.state.observe_world(self.b.mood or 'desktop', gentle=(kind != 'throw'), rough=(kind == 'throw'))
                self.state.save()
            return
        now = self.clock
        g = self.gestures
        if kind == 'double' and g and g[-1][1] == 'click' and now - g[-1][0] < 0.5:
            g[-1] = (now, 'double')                       # Qt sends click then double-click
        elif kind == 'click' and g and g[-1][1] == 'double' and now - g[-1][0] < 0.5:
            g[-1] = (now, 'triple')                       # a third press right after a double
        else:
            g.append((now, kind))
        if self.burst_start is None:
            self.burst_start = now
            self.burst_since = self.state.since_last() if self.state is not None else None
        self._touch(kind)

    def trigger(self, ttype, **extra):
        """a stand-alone event (its own generation, never grouped)"""
        if not self.enabled:
            if self.state is not None:
                self.state.record_background_interaction()
                self.state.observe_world(self.b.mood or 'desktop', gentle=(ttype not in ('poke_asleep','perch_lost')), rough=(ttype == 'poke_asleep'))
                self.state.save()
            return
        since = self.state.since_last() if self.state is not None else None
        self._touch(ttype)
        self._fire(ttype, extra, since)

    def roll_easter(self):
        if not self.active or self.cur is not None or self.pending is not None:
            return False
        p = (1.0 / 200.0) * (1.0 + DISP_FX * self.b.bias['mischievous'])
        if self.rng.random() < p:
            self.trigger('easter_egg')
            return True
        return False

    def note_span(self, kind, secs):
        if self.state is not None:
            self.state.note_span(kind, secs)
            self.state.save()

    # ---- per-frame
    def update(self, dt):
        self.clock += dt
        if not self.enabled:
            return
        if self.gestures and (self.clock - self.gestures[-1][0] >= self.BURST_QUIET or
                              self.clock - self.burst_start >= self.BURST_MAX):
            g, since = self.gestures, self.burst_since
            self.gestures, self.burst_start, self.burst_since = [], None, None
            self._fire_burst(g, since)
        if self.startup_due is not None and self.clock >= self.startup_due:
            self.startup_due = None
            self.trigger('long_idle_return', away=self.state.since_last() if self.state else None)
        self._poll()
        self._advance(dt)

    # ---- firing
    def _valid_variant(self, v):
        if v.get('sleep'):
            b = self.b
            return b.allow_sleep and not b.dragging and not b.is_sleeping()
        return True

    def _menu_for(self, key):
        base = TRIGGER_MENUS[key]
        ok = [a for a in base if any(self._valid_variant(v) for v in REACTIONS[a]['variants'])]
        # Never offer the immediately previous family when an honest alternative
        # exists. Variant selection also has its own no-repeat rule.
        choices = ok if len(ok) >= 2 else list(base)
        different = [a for a in choices if a != self.last_action]
        return different or choices

    def _fire_burst(self, g, since):
        counts = {}
        for _, k in g:
            counts[k] = counts.get(k, 0) + 1
        key = burst_menu_key(counts)
        self._fire('gesture_burst', dict(burst=counts), since, menu_key=key)

    def _fire(self, ttype, extra, since, menu_key=None):
        if not self.active:
            return
        st = self.state
        b = self.b
        st.observe_world(b.mood or 'desktop')
        st.sync()                                    # flush + really re-read the persisted summary
        fresh = bool(st.fresh)
        via = None
        if ttype == 'gesture_burst':
            via = ttype
            if fresh:                                # first touch after the overnight reset
                ttype, menu_key = 'first_of_day', 'first_of_day'
        elif ttype == 'mode_switch':
            menu_key = 'mode_' + str(extra.get('mode'))
        elif menu_key is None:
            menu_key = ttype
        if menu_key not in TRIGGER_MENUS:
            return
        menu = self._menu_for(menu_key)
        ctx = dict(trigger=ttype, via=via, burst=extra.get('burst', {}), mode=extra.get('mode'),
                   away=extra.get('away'), mood=b.mood, since_last=since, count_today=st.count,
                   disp=dict(st.disp), disp_word=disp_word(st.disp), fresh=fresh,
                   recent=st.recent_actions(3), sleeping=b.is_sleeping(), perched=(b.state == 'perch'),
                   memory=st.memory_card())
        intents = allowed_intents(menu)
        lines = lines_for_menu(menu)
        self.req_id += 1
        warm = bool(self.engine.loaded)
        budget = self.WAIT_WARM if warm else self.WAIT_COLD
        req = dict(id=self.req_id, menu=menu, intents=intents, lines=lines,
                   prompt=build_prompt(ctx, menu, intents, lines), grammar=build_grammar(menu, intents, lines),
                   deadline=time.monotonic() + budget)
        if not self.engine.submit(req):
            self.pending = None
            return
        self.stats['fired'] += 1
        self.pending = dict(id=self.req_id, menu=menu, intents=intents, lines=lines, ctx=ctx, due=self.clock + budget, trig=ttype,
                            urgent=ttype in URGENT_TRIGGERS)

    # ---- results
    def _poll(self):
        eng = self.engine
        if eng is None:
            return
        while True:
            res = eng.poll()
            if res is None:
                break
            p = self.pending
            if p is None or res['id'] != p['id']:
                self.stats['dropped'] += 1           # stale answer for a request we already gave up on
                continue
            self.pending = None
            if res['reason'] == 'unavailable':
                continue                             # layer degrades to "disabled" silently
            if res['ok']:
                self.stats['llm'] += 1
                self._play(res['action'], res.get('line'), p, res.get('intent'))
            else:
                self._fallback(p)
        p = self.pending
        if p is not None:
            if not eng.usable:
                self.pending = None
            elif self.clock >= p['due']:
                self.pending = None
                self._fallback(p)

    def _fallback(self, p):
        self.stats['fallback'] += 1
        action = self.rng.choice(p['menu'])
        self._play(action, None, p, random.choice(ACTION_INTENTS.get(action, INTENTS)))

    # ---- playback
    def _play(self, action, line, p, intent=None):
        if action not in REACTIONS:
            return
        if action == self.last_action and not p.get('urgent'):
            alternatives = [a for a in p.get('menu', ()) if a != action]
            if alternatives:
                action = self.rng.choice(alternatives)
                line = None  # the selected line belonged to the rejected action
        b = self.b
        bias = disp_biases(self.state.disp)
        v = pick_variant(action, bias, self.rng, self.last_variant.get(action), self._valid_variant)
        self.last_variant[action] = v['id']
        intent = intent if intent in ACTION_INTENTS.get(action, INTENTS) else ACTION_INTENTS.get(action, INTENTS)[0]
        rec = dict(action=action, intent=intent, v=v, line=line, trig=p['trig'], t=0.0, bi=0,
                   beats=sorted(v.get('beats', []), key=lambda x: x[0]))
        s = v.get('side')
        rec['sidemul'] = 1.0 if s is None else b.cursor_side() * s
        if self.cur is not None and not p.get('urgent'):
            self.queued = rec
        else:
            self._start(rec)

    def _start(self, rec):
        self.cur, self.queued = rec, None
        self.last_action = rec['action']
        b, st = self.b, self.state
        feel = REACTIONS[rec['action']]['feel']
        st.apply_feeling(feel)
        st.add_history(rec['trig'], rec['action'], feel)
        b.apply_intent(rec['intent'])
        b.bump_affection(REACTIONS[rec['action']]['aff'])
        # A reaction chooses one audio family once, rather than playing on every
        # spring beat.  The UI-side shuffle/cooldown keeps this pleasantly sparse.
        if rec['action'] in ('playful_spin', 'easter_egg_pirouette', 'easter_egg_confetti',
                             'easter_egg_blep', 'missed_you_stretch'):
            b.emit('sound', 'minecraft')
        elif rec['action'] in ('startled_hop', 'annoyed_wiggle'):
            b.emit('sound', 'minecraft')
        if rec['line']:
            # The spoken reaction carries its own drawn balloon silhouette.
            b.emit('bubble', (rec['line'], rec['action']))
        st.save()

    def _advance(self, dt):
        c = self.cur
        if c is None:
            return
        c['t'] += dt
        bs = c['beats']
        while c['bi'] < len(bs) and c['t'] >= bs[c['bi']][0]:
            self.b._beat(bs[c['bi']][1], c['sidemul'])
            c['bi'] += 1
        if c['t'] >= c['v']['dur']:
            self.cur = None
            if c['v'].get('sleep') and self._valid_variant(c['v']):
                self.b.go_sleep()
            if self.queued is not None:
                self._start(self.queued)

    def overlay(self):
        """eased-in targets of the playing variant, already scaled by its envelope"""
        c = self.cur
        if c is None:
            return None
        v, t = c['v'], c['t']
        dur = v['dur']
        env = smoothstep(t / 0.14) * (1.0 - smoothstep((t - (dur - 0.35)) / 0.35))
        s = c['sidemul']
        h = v.get('hold', {})
        o = dict(head=h.get('head', 0.0) * env * s, body=h.get('body', 0.0) * env * s,
                 lean=h.get('lean', 0.0) * env * s,
                 ear_l=(h.get('ear', 0.0) + h.get('ear_l', 0.0)) * env,
                 ear_r=(h.get('ear', 0.0) + h.get('ear_r', 0.0)) * env,
                 tail=h.get('tail', 0.0) * env, stretch=h.get('stretch', 0.0) * env,
                 tongue=(0.0, 0.0), blink=h.get('blink', 0.0) * env, lx=0.0, ly=0.0, lw=0.0)
        if 'tongue' in h:
            o['tongue'] = (h['tongue'][0], h['tongue'][1] * env)
        if 'look' in h:
            lx, ly, lw = h['look']
            o['lx'], o['ly'], o['lw'] = lx * (s if v.get('side') is not None else 1.0), ly, lw * env
        for ch, amp, hz in v.get('osc', ()):
            w = amp * math.sin(2.0 * math.pi * hz * t) * env
            if ch == 'ear':
                o['ear_l'] += w; o['ear_r'] += w
            elif ch == 'stretch':
                o['stretch'] += w * 0.01
            else:
                o[ch] += w
        return o


THROW_SPEED = 780.0     # release speed (px/s) that counts as a throw

# Distinct idle set-pieces.  Unlike reaction variants, these own a full pose
# silhouette over time as well as beats—so a viewer can recognize the act even
# in a still frame.
CHARACTER_ACTS = {
    'stalk': (2.55, [(0.00,dict(stretch=-7,ears=-170,tail=-120)),(.72,dict(hop=-65,lean=55)),(1.38,dict(hop=-70,tail=120)),(2.00,dict(hop=-95,fx=['q']))]),
    'pounce': (2.15, [(0.00,dict(stretch=-9,ears=-220,tail=-180)),(.55,dict(hop=-285,stretch=10,fx=['s'])),(1.20,dict(hop=-80,shake=.5,fx=['h']))]),
    'sneeze': (1.65, [(0.00,dict(head=-125,ears=-170)),(.52,dict(head=320,stretch=7,shake=.8,fx=['s','s'])),(.85,dict(head=-90,blink_pulse=True))]),
    'stargaze': (2.85, [(0.00,dict(head=150,ears=240,tail=100,fx=['s'])),(1.05,dict(head=80,fx=['s'])),(1.95,dict(head=-70,blink_pulse=True))]),
    'proud': (2.25, [(0.00,dict(stretch=9,ears=260,tail=180,fx=['h'])),(.82,dict(head=75,tail=110)),(1.55,dict(hop=-80,fx=['s']))]),
    'hide_peek': (2.65, [(0.00,dict(lean=-170,ears=-180)),(.62,dict(lean=210,head=100,ear_l=320,fx=['q'])),(1.42,dict(lean=-170)),(2.00,dict(lean=170,fx=['s']))]),
    'yoga': (3.10, [(0.00,dict(stretch=-10,ears=-240)),(.70,dict(stretch=13,tail=150)),(1.65,dict(stretch=-8,head=110)),(2.38,dict(stretch=7,fx=['h']))]),
    'dizzy': (2.55, [(0.00,dict(spin=(1.25,1.05,1),ears=340,fx=['s'])),(1.15,dict(head=170,shake=.5)),(1.72,dict(blink_pulse=True,fx=['q']))]),
    'bubble_dance': (2.80, [(0.00,dict(hop=-120,tail=170,fx=['s'])),(.36,dict(hop=-135,tail=-180)),(.72,dict(hop=-120,tail=170,fx=['s'])),(1.08,dict(hop=-135,tail=-180)),(1.56,dict(spin=(.45,.45,1),fx=['h']))]),
    'butterfly': (2.75, [(0.00,dict(ears=390,head=90,fx=['s'])),(.48,dict(hop=-145,lean=90,fx=['s'])),(1.10,dict(hop=-125,lean=-110,fx=['s'])),(1.70,dict(hop=-110,fx=['h']))]),
    'tail_knot': (2.30, [(0.00,dict(tail=300,head=120,fx=['q'])),(.55,dict(spin=(.62,.62,-1),tail=260)),(1.30,dict(tail=-240,head=-100,fx=['s']))]),
    'rain_shiver': (2.45, [(0.00,dict(ears=-260,stretch=-5,fx=['d'])),(.42,dict(shake=.7,tail=130)),(.92,dict(shake=.6,tail=-140)),(1.52,dict(hop=-100,fx=['s']))]),
}


class Brain(object):
    def __init__(self, W, H, S, mode='chill', sleepy=True):
        self.W, self.H, self.S = float(W), float(H), float(S)
        self.view_pad = 0.0            # extra transparent headroom added by the host
        self.mode = mode
        self.allow_sleep = sleepy
        self.t = 0.0
        self.x = 400.0; self.y = 400.0
        self.vx = 0.0; self.vy = 0.0
        self.bl, self.bt, self.br, self.bb = 0.0, 0.0, 1e5, 1e5
        self.dragging = False
        self.grab = (0.0, 0.0)
        self.state = 'idle'; self.state_t = 0.0; self.state_dur = 2.5
        self.target = (self.x, self.y)
        self.cycles = 0
        self.sleep_after = random.randint(5, 9)
        self.sleep = 0.0
        self.excite = 0.0; self.excite_hold = 0.0
        self.happy_t = 0.0
        self.spin_t = -1.0
        self.shake = 0.0
        self.lick_t = 0.0
        self.fun_t = 0.0; self.fun_kind = None
        self.character_act = None; self.act_t = 0.0; self.act_dur = 0.0
        self.act_beats = []; self.act_beat_i = 0
        self.last_cx = self.last_cy = 0.0; self.cursor_idle = 0.0
        self.near_t = 0.0
        # Cursor curiosity is voluntary in Chill mode; Follow mode remains explicit.
        self.cursor_curiosity = True
        self.cursor = (0.0, 0.0)
        self.cursor_speed = 0.0
        self.cursor_invite_in = random.uniform(3.0, 7.0)
        # springs
        self.sp_lean = Spring(0, 70, 9)
        self.sp_body = Spring(0, 55, 5)
        self.sp_head = Spring(0, 90, 10)
        self.sp_ear_l = Spring(0, 240, 14)
        self.sp_ear_r = Spring(0, 240, 14)
        self.sp_tail = Spring(0, 45, 4.5)
        self.sp_stretch = Spring(0, 200, 10)
        self.sp_hop = Spring(0, 150, 10)
        self.sp_tongue = Spring(0.76, 170, 13)
        self.sp_tsway = Spring(0, 120, 9)
        self.sp_lx = Spring(0, 110, 16)
        self.sp_ly = Spring(0, 110, 16)
        self.sp_bob = 0.0
        self.tail_phase = 0.0
        # blink / glance
        self.blink_in = random.uniform(1.5, 4.0); self.blink_t = -1.0
        self.glance_in = random.uniform(1.0, 3.0); self.glance = (0.0, 0.0)
        self.twitch_in = random.uniform(3, 7)
        self.lick_in = random.uniform(5, 11)
        self.sleep_twitch_in = random.uniform(5, 11)
        self.last_fun_kind = None
        self.fx = []
        self.z_in = 0.5
        self.ground_f = 0.0
        self.wins = []; self.mood = None; self.fg = None
        self.perch_id = None; self.perch_dx = 0.0; self.perch_rect = None
        self._carried = False
        # --- animation pack (pet_anims.py): additive clips over the pose we build
        self.anim = pet_anims.AnimPlayer() if pet_anims is not None else None
        self.anim_enabled = True       # user setting; the pack is optional and safe to switch off
        self.anim_travel = False       # scripted screen travel is opt-in
        self.anim_failed = False       # fail-safe latch: a renderer/clip error disables the layer
        self.fullscreen = False        # set by the host window poller
        self.low_fps = False           # set by the host tick; skips busy clips on slow frames
        self.mv = None                 # scripted window travel for rocket / teleport
        self.mv_stack = []
        self.on_sound = None           # optional host hook; events are emitted otherwise
        # --- reaction layer hooks (all inert until Reactor.enabled)
        self.rx = Reactor(self)
        self.events = []                       # ('sound', kind) / ('bubble', text) for the UI layer
        self.affection = 0.0                   # short-term, deterministic, drives the blush
        self.bias = dict((k, 0.0) for k in DISP_KEYS)
        self.spin_dur, self.spin_deg = 0.8, 360.0
        self.ph_bob = self.ph_bob2 = 0.0
        self.gaze_wander = 0.0
        self.cur_dx = 0.0
        self.motion_scale = 1.0
        self.energy_level = 0.65
        self.weather_mood = 'off'  # manual/optional: off, rain, clear
        self.code_context = ''     # RAM-only, set only by explicit VS Code opt-in
        self.allow_icon_autonomy = False
        self.click_times = []
        self._dyn_springs = (self.sp_head, self.sp_ear_l, self.sp_ear_r, self.sp_tail)

    # ------------------------------------------------------------ setup
    def set_size(self, W, H, S):
        self.W, self.H, self.S = float(W), float(H), float(S)

    def animation_pad(self):
        """Design-px headroom a playing pack clip needs (0 when idle).

        The host grows the window by this much on every side for the duration
        of a clip; the base window stays compact, so the pet never blocks a
        larger rectangle of the desktop while it is just sitting still.
        """
        if (self.anim is not None and self.anim.active and
                self.anim_enabled and not self.anim_failed):
            return 92.0    # jack_in_box peaks at ~86 design px above the base box
        return 0.0

    def place(self, x, y):
        self.x, self.y = float(x), float(y)
        self.target = (self.x, self.y)

    def set_mode(self, mode, user=False):
        if user and mode != self.mode:
            self.rx.trigger('mode_switch', mode=mode)
        self.mode = mode
        if mode == 'follow':
            if self.state in ('sleep', 'tosleep'):
                self._wake()
            elif self.state != 'follow':
                self._begin('follow')
        else:
            if self.state in ('follow', 'cursor_visit'):
                self._begin('idle', random.uniform(1, 3))

    def stop_cursor_behavior(self, disable=True):
        """Immediately cancel cursor following/visits, optionally preserving the toggle."""
        if disable:
            self.cursor_curiosity = False
            self.cursor_invite_in = 1e9
        if self.state in ('follow', 'cursor_visit'):
            self._begin('idle', random.uniform(1.0, 2.0))
            self._brake(0.12, 8.0)

    # ------------------------------------------------------------ input events
    def begin_drag(self, cx, cy):
        self.dragging = True
        if self.anim is not None:
            self.anim.stop(0.1)
        self.mv = None; self.mv_stack = []
        self.grab = (self.x - cx, self.y - cy)
        if self.state in ('sleep', 'tosleep'):
            self._wake(quiet=True)
        self._begin('idle', 1.0)
        self.excite = max(self.excite, 0.8); self.excite_hold = 1.0
        self.sp_stretch.kick(5.0)
        self.sp_tongue.t = 1.1
        self.emit('sound', 'grab')

    def end_drag(self):
        self._release_gesture(math.hypot(self.vx, self.vy))
        self.dragging = False
        self.excite_hold = 0.8
        self.sp_tongue.t = 0.76
        self.vx = clamp(self.vx, -900, 900); self.vy = clamp(self.vy, -900, 900)
        self._begin('idle', random.uniform(2.5, 5))

    def click(self):
        # A poke interrupts an ordinary clip; travel clips are allowed to finish
        # so their scripted window path can always return to the ground.
        if self.anim is not None and self.anim.playing:
            c = pet_anims.CLIPS.get(self.anim.clip.name) if pet_anims is not None else None
            if c is not None and not c.travel:
                self.anim.stop(0.18)
        asleep = self.state in ('sleep', 'tosleep')
        self._note_click(asleep)
        if asleep:
            self._wake(startle=True)
            self.emit('sound', 'startle')
            self.rx.trigger('poke_asleep')
        else:
            self.cheer()
            self.emit('sound', 'click')
            self.rx.gesture('click')

    def double_click(self):
        if self.state in ('sleep', 'tosleep'):
            self._wake(startle=True)
        self.cheer()
        self._spin()
        self.bump_affection(0.05)
        self.emit('sound', 'double')
        self.rx.gesture('double')

    def cheer(self):
        self.excite = 1.0; self.excite_hold = 1.8
        self.happy_t = 0.9
        self.lick_t = 1.1
        self.sp_stretch.kick(7.0)
        self.sp_hop.kick(-210.0)
        self.sp_ear_l.kick(400.0); self.sp_ear_r.kick(400.0)
        self.shake = max(self.shake, 0.7)
        for i in range(3):
            a = random.uniform(-2.6, -0.5)
            sp = random.uniform(40, 70)
            self.fx.append(dict(kind='s', x=math.cos(a)*60 + random.uniform(-40, 40), y=-120 + math.sin(a)*25,
                                vx=math.cos(a)*sp, vy=math.sin(a)*sp, age=0.0, life=random.uniform(0.7, 1.1),
                                size=random.uniform(6, 10), rot=random.uniform(-20, 20)))

    def companion_event(self, event):
        """Apply safe visual-only reactions from the optional local adapters."""
        kind = event.get('event', '')
        if kind in ('youtube_play', 'youtube_music'):
            self.mood = 'music' if kind == 'youtube_music' else 'video'
            self.fun_kind = 'wiggle'; self.fun_t = 0.0
            self.excite = max(self.excite, 0.34); self.excite_hold = max(self.excite_hold, 1.2)
            self._emit_fx('s')
        elif kind == 'youtube_pause':
            self.fun_kind = 'look'; self.fun_t = 0.0; self._emit_fx('q')
        elif kind == 'youtube_fullscreen':
            self.mood = 'video'; self.sp_ear_l.kick(-120); self.sp_ear_r.kick(-120)
        elif kind == 'saved':
            self.sp_ear_l.kick(220); self.sp_ear_r.kick(220); self._emit_fx('s')
        elif kind == 'build_ok':
            self.cheer(); self._spin(dur=.62, turns=.55, dirn=1); self._emit_fx('h')
        elif kind == 'build_error':
            self.sp_head.kick(-70); self._emit_fx('q')
        elif kind == 'document_context':
            # Current-document text remains in RAM only. The pet stores a bounded
            # excerpt for this session and reacts as a quiet reading companion.
            self.code_context = event.get('context','')[:12288]
            self.mood = 'work'; self.fun_kind = 'look'; self.fun_t = 0.0
            self.sp_ear_l.kick(95); self.sp_ear_r.kick(95)

    def go_sleep(self):
        if self.state not in ('sleep', 'tosleep'):
            self._begin_tosleep()
            self.emit('sound', 'sleep')

    def wake_up(self):
        if self.state in ('sleep', 'tosleep'):
            self._wake()

    def is_sleeping(self):
        return self.state in ('sleep', 'tosleep')

    # ------------------------------------------------------------ reaction layer hooks
    def emit(self, kind, payload=None):
        if len(self.events) < 32:
            self.events.append((kind, payload))

    def drain_events(self):
        ev, self.events = self.events, []
        return ev

    def bump_affection(self, d):
        if d >= 0:
            self.affection += d * (1.0 - self.affection)
        else:
            self.affection += d
        self.affection = clamp(self.affection, 0.0, 1.0)

    def _note_click(self, asleep):
        now = self.t
        self.click_times = [c for c in self.click_times if now - c < 3.0] + [now]
        if asleep:
            self.bump_affection(-0.02)
        elif len(self.click_times) >= 5:
            self.bump_affection(-0.04)          # too much poking
        else:
            self.bump_affection(0.07)

    def _release_gesture(self, speed):
        if speed > THROW_SPEED:
            self.bump_affection(-0.04)
            self.emit('sound', 'throw')
            self.rx.gesture('throw')
        else:
            self.bump_affection(0.05)
            self.emit('sound', 'land')
            self.rx.gesture('drag')

    def apply_intent(self, intent):
        """Intent is a safe, deterministic bridge from the local model to body language."""
        if intent == 'watch_cursor':
            self.fun_kind = 'look'; self.fun_t = 0.0
            self.sp_ear_l.kick(220.0); self.sp_ear_r.kick(220.0)
        elif intent == 'rest_nearby':
            self.bump_affection(0.025); self.sp_tail.kick(45.0)
        elif intent == 'ask_attention':
            self._emit_fx('q'); self.excite=max(self.excite,0.48); self.excite_hold=max(self.excite_hold,0.8)
        elif intent == 'play':
            self.excite=max(self.excite,0.72); self.excite_hold=max(self.excite_hold,1.1)
            self._emit_fx('s')
            # The model only proposes this intent from its constrained grammar.
            # UI permissions and a cooldown decide whether an actual carry starts.
            if self.allow_icon_autonomy:
                self.emit('icon_candidate')
        elif intent == 'settle':
            self.sp_stretch.kick(-2.5)

    def cursor_side(self):
        return 1.0 if self.cur_dx >= 0 else -1.0

    def _spin(self, dur=0.8, turns=1.0, dirn=1):
        self.spin_t = 0.0
        self.spin_dur = max(0.2, dur)
        self.spin_deg = 360.0 * turns * dirn

    def _fx(self, kind, x, y, vx, vy, life, size, rot=0.0):
        self.fx.append(dict(kind=kind, x=x, y=y, vx=vx, vy=vy, age=0.0, life=life, size=size, rot=rot))

    def _emit_fx(self, kind):
        r = random.uniform
        sg = 1.0 if random.random() < 0.5 else -1.0
        if kind == 'b':
            self._fx('b', sg * r(40, 90), -168, 0, -6, 0.95, 11, r(-8, 8))
        elif kind == 'q':
            self._fx('q', r(55, 100), -168, 0, -10, 1.3, 11, r(-10, 10))
        elif kind == 'a':
            self._fx('a', r(95, 125), -118, 0, -4, 1.0, 9, 0)
        elif kind == 'd':
            self._fx('d', -r(95, 125), -116, 0, 24, 0.9, 7, 0)
        elif kind == 'h':
            self._fx('h', r(-70, 70), -135, r(-12, 12), -r(26, 40), 1.5, 9, r(-12, 12))
        elif kind == 's':
            self._fx('s', r(-70, 70), -125, r(-30, 30), -r(20, 50), r(0.7, 1.1), r(6, 10), r(-20, 20))
        elif kind == 'z':
            self._fx('z', 58, -150, 12, -18, 2.6, 8.0, -8)

    def _beat(self, b, side=1.0):
        """one-shot kicks from a reaction variant"""
        ms = self.motion_scale
        if 'hop' in b: self.sp_hop.kick(b['hop'] * ms)
        if 'stretch' in b: self.sp_stretch.kick(b['stretch'] * ms)
        if 'ears' in b:
            self.sp_ear_l.kick(b['ears']); self.sp_ear_r.kick(b['ears'])
        if 'ear_l' in b: self.sp_ear_l.kick(b['ear_l'])
        if 'ear_r' in b: self.sp_ear_r.kick(b['ear_r'])
        if 'head' in b: self.sp_head.kick(b['head'] * side * ms)
        if 'body' in b: self.sp_body.kick(b['body'] * side * ms)
        if 'lean' in b: self.sp_lean.kick(b['lean'] * side * ms)
        if 'tail' in b: self.sp_tail.kick(b['tail'] * ms)
        if 'shake' in b: self.shake = max(self.shake, b['shake'])
        if 'spin' in b:
            turns, dur, dirn = b['spin']
            self._spin(dur, turns, dirn)
        if 'excite' in b:
            self.excite = max(self.excite, b['excite'][0]); self.excite_hold = max(self.excite_hold, b['excite'][1])
        if b.get('happy'): self.happy_t = b['happy']
        if b.get('lick'): self.lick_t = 1.1
        if 'aff' in b: self.bump_affection(b['aff'])
        if b.get('blink_pulse') and self.blink_t < 0: self.blink_t = 0.0
        for k in b.get('fx', ()):
            self._emit_fx(k)

    def _apply_overlay(self, ov, sl):
        self.sp_head.t += ov['head']
        self.sp_body.t += ov['body']
        self.sp_lean.t += ov['lean']
        self.sp_ear_l.t += ov['ear_l']
        self.sp_ear_r.t += ov['ear_r']
        self.sp_tail.t += ov['tail']
        self.sp_stretch.t += ov['stretch']
        tv, tw = ov['tongue']
        if tw > 0.0:
            self.sp_tongue.t = lerp(self.sp_tongue.t, tv, clamp(tw, 0.0, 1.0))

    def _think_step(self, dt):
        """reaction layer per-frame work: results/animation, disposition -> spring constants, affection decay"""
        self.rx.update(dt)
        tb = self.rx.target_bias()
        k = min(1.0, dt * 0.6)
        for key in DISP_KEYS:
            self.bias[key] += (tb[key] - self.bias[key]) * k
        bm, bs = self.bias['mischievous'], self.bias['sleepy']
        km = clamp(1.0 + DISP_FX * (bm - 0.6 * bs), 1.0 - DISP_FX, 1.0 + DISP_FX)   # mischievous: snappier
        cm = clamp(1.0 + DISP_FX * (bs - bm), 1.0 - DISP_FX, 1.0 + DISP_FX)          # sleepy: heavier damping
        for sp in self._dyn_springs:
            sp.k = sp.k0 * km
            sp.c = sp.c0 * cm
        self.affection *= math.exp(-dt / 140.0)

    # ------------------------------------------------------------ state helpers
    def _begin(self, st, dur=None):
        if self.state in ('sleep', 'perch') and st != self.state:
            self.rx.note_span(self.state, self.state_t)
        self.state = st; self.state_t = 0.0
        self.state_dur = dur if dur is not None else random.uniform(3.0, 8.0)

    def _begin_tosleep(self):
        if self.anim is not None:
            self.anim.stop(0.25)
        self.mv = None; self.mv_stack = []
        tx = random.uniform(self.bl, self.br)
        self.target = (tx, self.bb)
        self._begin('tosleep', 60)

    def _wake(self, startle=False, quiet=False):
        self._begin('wake', 1.5)
        self.cycles = 0
        self.sleep_after = random.randint(5, 9)
        if startle:
            self.sp_hop.kick(-300.0); self.sp_stretch.kick(9.0)
            self.excite = 0.9; self.excite_hold = 1.2
            self.sp_ear_l.kick(500.0); self.sp_ear_r.kick(500.0)
            self.shake = 0.5
        elif not quiet:
            self.sp_stretch.kick(6.0)
            self.lick_t = 1.2
            self.excite = 0.3; self.excite_hold = 0.6

    def _cursor_visit_ok(self):
        """Decide occasionally—not every frame—whether Chill wants to approach the cursor."""
        if not self.cursor_curiosity or self.mode != 'chill' or self.mood == 'video':
            return False
        if self.cursor_idle < 0.55 or self.cursor_speed > 280.0:
            return False
        cx, cy = self.cursor
        d = math.hypot(cx - self.x, cy - self.y)
        if d < 135.0 * max(0.7, self.S / 0.5) or d > 820.0:
            return False
        # Mischievous / affectionate days are a little keener, bounded at 80%.
        want = 0.42 + 0.16 * max(0.0, self.bias['mischievous']) + 0.12 * max(0.0, self.bias['affectionate'])
        return random.random() < min(0.80, want)

    def _begin_cursor_visit(self):
        self._begin('cursor_visit', random.uniform(2.8, 5.0))
        self.sp_ear_l.kick(310.0); self.sp_ear_r.kick(310.0)
        self.excite = max(self.excite, 0.42); self.excite_hold = max(self.excite_hold, 0.7)
        self._emit_fx('q')

    def _pick_next(self):
        hour = datetime.datetime.now().hour
        # Local clock only: a gentle daily rhythm, no network or location data.
        if 5 <= hour < 10 and random.random() < .28:
            self.sp_stretch.kick(7.0); self._begin('idle', random.uniform(2, 4)); return
        if 22 <= hour or hour < 5:
            if self.allow_sleep and random.random() < .48:
                self._begin_tosleep(); return
        elif 13 <= hour < 18:
            self.energy_level = max(self.energy_level, .72)
        if self.mode == 'follow':
            self._begin('follow'); return
        if self.mode == 'chill' and self.cursor_curiosity and self.cursor_invite_in <= 0.0:
            self.cursor_invite_in = random.uniform(8.0, 16.0)
            if self._cursor_visit_ok():
                self._begin_cursor_visit(); return
        if self.mode == 'still':
            self._begin('idle', random.uniform(3, 7)); self._fun(); return
        self.cycles += 1
        if self.allow_sleep and self.cycles >= self.sleep_after:
            self._begin_tosleep(); return
        if self.weather_mood == 'rain' and random.random() < .38:
            self._begin('idle', random.uniform(7, 14)); self.fun_kind = 'look'; self.fun_t = 0.0; return
        if self.mood in ('video', 'game'): # calm/cautious around immersive apps
            self._begin('idle', random.uniform(6, 12)); return
        if self.wins and random.random() < (0.6 if self.mood == 'work' else 0.25):
            if self._start_perch():
                return
        # Revisit a symbolic screen corner sometimes; coordinates are recalculated for each screen.
        if self.mode == 'chill' and self.rx.state is not None and random.random() < 0.16:
            corners = {'lower left corner': (self.bl + 70*self.S, self.bb - 35*self.S),
                       'lower right corner': (self.br - 70*self.S, self.bb - 35*self.S),
                       'quiet center': ((self.bl+self.br)/2, (self.bt+self.bb)/2)}
            known = [p['name'] for p in self.rx.state.places if p.get('name') in corners]
            if known:
                self.target = corners[random.choice(known)]
                self._begin('wander', 40)
                return
        if random.random() < 0.78:
            for _ in range(12):
                tx = random.uniform(self.bl, self.br)
                ty = lerp(self.bt, self.bb, random.random() ** 0.6)
                if math.hypot(tx - self.x, ty - self.y) > 220 * max(0.6, self.S / 0.5) or _ == 11:
                    break
            self.target = (tx, ty)
            if self.rx.state is not None:
                if tx < self.bl + (self.br-self.bl)*0.22 and ty > self.bb - (self.bb-self.bt)*0.25:
                    self.rx.state.note_place('lower left corner', self.mood or 'desktop')
                elif tx > self.br - (self.br-self.bl)*0.22 and ty > self.bb - (self.bb-self.bt)*0.25:
                    self.rx.state.note_place('lower right corner', self.mood or 'desktop')
                self.rx.state.save()
            self._begin('wander', 40)
        else:
            self._begin('idle', random.uniform(3, 6)); self._fun()

    def _fun(self):
        if self.rx.roll_easter():
            return
        # The animation pack gets first refusal on an idle slot.  It only runs
        # here, when the pet is already idle, so reactions, naps, perches and
        # walks keep priority; travel clips stay on the menu in Still mode.
        if (self.anim is not None and self.anim_enabled and not self.anim_failed and
                not self.character_act and not self.fullscreen and
                self.mood not in ('video', 'game') and random.random() < 0.55):
            tags = ('idle', 'playful', 'calm', 'chill', 'curious', 'show', 'greet', 'affection')
            skip = ()
            if (not self.anim_travel or self.mode == 'still' or self.motion_scale < 1.0 or
                    self.mood == 'work'):
                skip = tuple(n for n, c in pet_anims.CLIPS.items() if c.travel)
            quiet = self.motion_scale < 1.0 or self.low_fps or self.energy_level < 0.5
            if quiet:
                skip += tuple(n for n, c in pet_anims.CLIPS.items()
                              if ('show' in c.tags or 'playful' in c.tags) and n not in skip)
            name = self.anim.pick(tags, exclude=skip)
            if name and self.play_anim(name):
                self.last_fun_kind = 'anim'
                return
        # Named idle sketches—each is a deliberate mini-performance with a
        # different silhouette and recovery, never a generic idle loop.
        choices = ['look', 'wiggle', 'lick', 'spin', 'hop', 'bow', 'tail_chase', 'peekaboo', 'moonwalk', 'balance', 'sprint', 'investigate'] + list(CHARACTER_ACTS)
        choices = [x for x in choices if x != self.last_fun_kind] or choices
        kind = random.choice(choices)
        self.last_fun_kind = kind
        if kind in CHARACTER_ACTS:
            self._start_character_act(kind)
        elif kind == 'look':
            self.fun_kind = 'look'; self.fun_t = 0.0
        elif kind == 'wiggle':
            self.fun_kind = 'wiggle'; self.fun_t = 0.0
            self.excite = max(self.excite, 0.7); self.excite_hold = 1.6
            self.shake = 0.5
        elif kind == 'lick':
            self.lick_t = 1.1
        elif kind == 'spin':
            self._spin()
            self.sp_stretch.kick(4.0)
        elif kind == 'hop':
            self.fun_kind = None
            self.sp_hop.kick(-180.0); self.sp_stretch.kick(5.0)
        elif kind == 'bow':
            self.fun_kind = 'look'; self.fun_t = 0.0
            self.sp_stretch.kick(-8); self.sp_head.kick(120); self.sp_ear_l.kick(-200); self.sp_ear_r.kick(-200)
            self._emit_fx('h')
        elif kind == 'tail_chase':
            self._spin(dur=1.05, turns=.85, dirn=random.choice((-1,1)))
            self.sp_tail.kick(300); self.sp_hop.kick(-115); self._emit_fx('q')
        elif kind == 'peekaboo':
            self.fun_kind = 'look'; self.fun_t = 0.0
            self.sp_lean.kick(random.choice((-1,1))*150); self.sp_head.kick(90); self.sp_ear_l.kick(280); self._emit_fx('q')
        elif kind == 'moonwalk':
            self.target = (clamp(self.x + random.choice((-1,1))*130*self.S,self.bl,self.br), self.y)
            self._begin('wander', 2.2); self.sp_tail.kick(-180); self.sp_stretch.kick(-3)
        elif kind == 'sprint':
            # Distinct three-part run: crouch anticipation -> fast curved dash
            # -> intentional overshoot/recovery.  It is not a scaled wander.
            d = random.choice((-1, 1))
            self.sp_stretch.kick(-9); self.sp_lean.kick(-d * 155); self.sp_tail.kick(-d * 260)
            self.target = (clamp(self.x + d * random.uniform(300, 520) * self.S, self.bl, self.br),
                           clamp(self.y + random.uniform(-75, 75) * self.S, self.bt, self.bb))
            self.vx += d * 310 * self.S; self.vy += random.uniform(-45,45) * self.S
            self._begin('sprint', 1.35); self.excite = 1.0; self.excite_hold = 1.5; self._emit_fx('s')
        elif kind == 'investigate':
            # A character-specific investigate: ear radar, low forward lean,
            # small peek-hop, then a held off-centre gaze.
            self.fun_kind = 'investigate'; self.fun_t = 0.0
            self.sp_ear_l.kick(390); self.sp_ear_r.kick(-300); self.sp_lean.kick(120)
            self.sp_head.kick(100); self.sp_tail.kick(115); self._emit_fx('q')
        else: # balance
            self.fun_kind = 'wiggle'; self.fun_t = 0.0
            self.sp_hop.kick(-85); self.sp_lean.kick(140); self.sp_tail.kick(-220); self._emit_fx('s')

    def _start_character_act(self, name):
        self.character_act = name; self.act_t = 0.0; self.act_beat_i = 0
        self.act_dur, self.act_beats = CHARACTER_ACTS[name]
        self.fun_kind = None; self.fun_t = 0.0

    def _step_character_act(self, dt):
        if not self.character_act:
            return
        self.act_t += dt
        while self.act_beat_i < len(self.act_beats) and self.act_t >= self.act_beats[self.act_beat_i][0]:
            self._beat(self.act_beats[self.act_beat_i][1])
            self.act_beat_i += 1
        if self.act_t >= self.act_dur:
            self.character_act = None; self.act_beats = []

    def _apply_character_act_pose(self, P):
        """Pose language distinct enough to identify the act from one frame."""
        name = self.character_act
        if not name: return P
        u = clamp(self.act_t / max(.1, self.act_dur), 0.0, 1.0)
        wave = math.sin(self.act_t * math.pi * 2.0)
        if name == 'stalk':
            P['sy'] *= .86; P['sx'] *= 1.12; P['head_dy'] += 11; P['ear_l'] -= 22; P['ear_r'] -= 22; P['look_x'] = .95
        elif name == 'pounce':
            q = smoothstep(min(1.0, u/.30)); P['sy'] *= 1-.12*q; P['sx'] *= 1+.10*q; P['tail_rot'] -= 85*q
        elif name == 'sneeze':
            P['head_rot'] += -28 if u < .31 else 42*math.exp(-((u-.34)/.12)**2); P['blink'] = max(P['blink'], .88 if .30<u<.50 else 0)
        elif name == 'stargaze':
            P['head_rot'] += 27; P['head_dy'] -= 8; P['ear_l'] += 18; P['ear_r'] += 18; P['look_y'] = -1.0
        elif name == 'proud':
            P['sy'] *= 1.09; P['sx'] *= .94; P['head_rot'] += 12; P['tail_rot'] += 38; P['halo'] = 1.0
        elif name == 'hide_peek':
            side = -1 if int(self.act_t/.62)%2==0 else 1; P['ox'] += side*48*self.S; P['head_rot'] += -side*21; P['ear_l'] -= 18; P['ear_r'] -= 18; P['look_x'] = -side
        elif name == 'yoga':
            P['sy'] *= 1 + .13*math.sin(math.pi*u); P['sx'] *= 1 - .08*math.sin(math.pi*u); P['head_dy'] += 12*math.sin(math.pi*u)
        elif name == 'dizzy':
            P['rot'] += 9*math.sin(self.act_t*8.0)*(1-u); P['look_x'] = math.sin(self.act_t*9.0); P['look_y'] = math.cos(self.act_t*9.0)*.55
        elif name == 'bubble_dance':
            P['ox'] += 15*self.S*wave; P['rot'] += 7*wave; P['tail_rot'] += 105*wave; P['tongue'] = 1.15
        elif name == 'butterfly':
            P['head_rot'] += 14*wave; P['ear_l'] += 22*wave; P['ear_r'] -= 22*wave; P['look_x'] = math.sin(self.act_t*3.8)
        elif name == 'tail_knot':
            P['rot'] += 12*math.sin(self.act_t*5.5); P['tail_rot'] += 190; P['look_x'] = -.9
        elif name == 'rain_shiver':
            P['sx'] *= 1+.035*math.sin(self.act_t*22); P['sy'] *= 1-.035*math.sin(self.act_t*22); P['ear_l'] -= 24; P['ear_r'] -= 24
        return P

    def _goto(self, tx, ty, dt, vmax, accel=3.0):
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 1e-3:
            return 0.0
        sp = min(vmax, 28.0 + dist * 1.7)
        dvx, dvy = dx / dist * sp, dy / dist * sp
        wob = math.sin(self.t * 1.6) * 16.0 * min(1.0, dist / 220.0)
        dvx += -dy / dist * wob; dvy += dx / dist * wob
        k = min(1.0, accel * dt)
        self.vx += (dvx - self.vx) * k
        self.vy += (dvy - self.vy) * k
        return dist

    # ------------------------------------------------------------ animation pack hooks
    def anim_allowed(self, clip, force=False):
        """Cheap policy gate for pack clips (the fail-safe lives in update())."""
        if self.anim is None or pet_anims is None or self.anim_failed or not self.anim_enabled:
            return False
        if clip is None:
            return False
        if clip.travel and not self.anim_travel and not force:
            return False
        if force:
            return True
        if self.fullscreen or self.mood in ('video', 'game'):
            return False
        if clip.travel and self.mood == 'work':
            return False
        busy = ('show' in clip.tags or 'playful' in clip.tags or clip.travel)
        if self.low_fps and busy:
            return False
        if self.motion_scale < 1.0 and busy:
            return False
        if self.energy_level < 0.5 and ('show' in clip.tags or 'playful' in clip.tags):
            return False
        return True

    def play_anim(self, name, force=False, **opts):
        """Start one pet_anims clip; True only when it actually started.

        Normal calls take a free idle slot, so reactions, naps, perches and
        walks are never hijacked.  force=True is the menu path: it leaves those
        states, but refuses to interrupt a travel clip that is still airborne.
        """
        if self.anim is None or pet_anims is None or self.dragging:
            return False
        clip = pet_anims.CLIPS.get(name)
        if clip is None or not self.anim_allowed(clip, force=force):
            return False
        if force:
            if self.state == 'icon_carry':
                return False          # the carried icon is already in the air
            if self.anim.playing:
                cur = pet_anims.CLIPS.get(self.anim.clip.name)
                if cur is not None and cur.travel and self.anim.stopping is None:
                    return False          # let the scripted path land first
            if self.state in ('sleep', 'tosleep'):
                self._wake(quiet=True)
            if self.state != 'idle' or self.character_act:
                self.perch_id = None
                self.character_act = None; self.act_beats = []
                self._begin('idle', clip.dur + 1.5)
            self.mv = None; self.mv_stack = []
        elif self.state != 'idle' or self.mv or self.character_act:
            return False
        if not self.anim.play(name, restart=bool(force), **opts):
            return False
        self.fun_kind = None; self.fun_t = 0.0
        self.state_dur = max(self.state_dur - self.state_t, clip.dur + 0.8)
        self.state_t = 0.0
        return True

    def _step_mv(self, dt):
        """Scripted travel from the pack: the window follows an eased path."""
        m = self.mv
        if not m:
            return False
        m['t'] += dt
        u = clamp(m['t'] / m['dur'], 0.0, 1.0)
        e = pet_anims.EASE.get(m['ease'], pet_anims.sm)(u)
        nx = m['x0'] + (m['x1'] - m['x0']) * e
        ny = m['y0'] + (m['y1'] - m['y0']) * e
        k = min(1.0, dt * 10.0)
        self.vx += ((nx - self.x) / max(1e-4, dt) - self.vx) * k
        self.vy += ((ny - self.y) / max(1e-4, dt) - self.vy) * k
        self.x, self.y = nx, ny
        if u >= 1.0:
            # Land exactly on the target and stop: leaving the eased velocity
            # behind would make the ordinary idle integrator drift the pet.
            self.x, self.y = m['x1'], m['y1']
            self.vx = self.vy = 0.0
            self.mv = None
        return True

    def _mv_start(self, x1, y1, dur, ease):
        self.mv = dict(t=0.0, dur=max(0.05, dur), x0=self.x, y0=self.y,
                       x1=clamp(x1, self.bl, self.br), y1=clamp(y1, self.bt, self.bb), ease=ease)

    def _anim_request(self, r):
        """Host side of a clip cue: travel paths and named sound hooks."""
        k = r[0]
        if k == 'mark':
            self.mv_stack.append((self.x, self.y))
        elif k == 'move':
            # Chained relative move (zoomies / loop-the-loop / yoyo).  The
            # target is clamped to the pet's own bounds, exactly like _mv_start.
            self._mv_start(self.x + r[1] * self.S, self.y + r[2] * self.S,
                           r[3], r[4] if len(r) > 4 else 'lin')
        elif k == 'push':
            self.mv_stack.append((self.x, self.y))
            self._mv_start(self.x + r[1] * self.S, self.y + r[2] * self.S, r[3], r[4])
        elif k == 'pop' and self.mv_stack:
            x1, y1 = self.mv_stack.pop()
            self._mv_start(x1, y1, r[1], r[2])
        elif k == 'teleport':
            self.mv = None; self.mv_stack = []
            self.x = random.uniform(self.bl, self.br)
            self.y = lerp(self.bt, self.bb, random.random() ** 0.7)
            self.vx = self.vy = 0.0
            self.target = (self.x, self.y)
        elif k == 'sound':
            if self.on_sound is not None:
                try:
                    self.on_sound(r[1])
                except Exception:
                    pass
            else:
                self.emit('sound', r[1])

    def _find_perch(self):
        for w in self.wins:
            if w['id'] == self.perch_id:
                return w['rect']
        return None

    def _perch_point(self, r):
        S = self.S
        lo = r[0] + 50.0 * S
        x = clamp(r[0] + self.perch_dx, lo, max(lo, r[2] - 50.0 * S))
        return x, r[1] - EXT_B * S + 3.0 * S

    def _start_perch(self):
        S = self.S
        cands = []
        for w in self.wins:
            r = w['rect']
            if r[2] - r[0] >= 260 * S and r[1] - EXT_B * S + 3 * S >= self.bt and r[1] - EXT_B * S <= self.bb:
                cands.append(w)
        if not cands:
            return False
        w = random.choice(cands)
        self.perch_id = w['id']
        if self.rx.state is not None:
            self.rx.state.note_place('a window perch', self.mood or 'desktop')
            self.rx.state.save()
        self.perch_dx = random.uniform(0.15, 0.85) * (w['rect'][2] - w['rect'][0])
        self._begin('perch_go', 40)
        return True

    def _update_perch(self, dt):
        r = self.perch_rect
        if r is None or self.state_t >= self.state_dur:
            lost = r is None
            self.perch_id = None
            if lost:   # window closed / minimised under his feet
                self.sp_hop.kick(-260.0); self.sp_stretch.kick(8.0)
                self.excite = 0.9; self.excite_hold = 1.2; self.shake = 0.5
                self.sp_ear_l.kick(450.0); self.sp_ear_r.kick(450.0)
                self.emit('sound', 'startle')
                self.rx.trigger('perch_lost')
            else:
                self.sp_hop.kick(-150.0)
            self._begin('idle', random.uniform(2, 4))
            return
        tx, ty = self._perch_point(r)
        k = min(1.0, dt * 18.0)
        nx, ny = self.x + (tx - self.x) * k, self.y + (ty - self.y) * k
        kv = min(1.0, dt * 10.0)
        self.vx += ((nx - self.x) / dt - self.vx) * kv
        self.vy += ((ny - self.y) / dt - self.vy) * kv
        self.x, self.y = nx, ny
        self._carried = True

    def _brake(self, dt, rate=2.4):
        k = min(1.0, rate * dt)
        self.vx -= self.vx * k; self.vy -= self.vy * k

    # ------------------------------------------------------------ main update
    def update(self, dt, cursor, bounds, windows=None, mood=None, fg=None, perch_rect=None):
        dt = clamp(dt, 0.0, 0.05)
        if dt <= 0.0:
            return
        self.t += dt
        self.wins = windows or []; self.mood = mood; self.fg = fg; self.perch_rect = perch_rect
        self._carried = False
        cx, cy = cursor
        S = self.S
        l, t, r, b = bounds
        self.bl, self.bt = l + EXT_L * S, t + self.H / 2.0 - self.view_pad * S
        self.br, self.bb = max(self.bl, r - EXT_R * S), max(self.bt, b - EXT_B * S)
        moved = math.hypot(cx - self.last_cx, cy - self.last_cy)
        self.cursor = (cx, cy)
        if self.t > 0.12:
            raw_speed = moved / max(0.001, dt)
            self.cursor_speed += (raw_speed - self.cursor_speed) * min(1.0, dt * 8.0)
        if moved > 2:
            if self.cursor_idle > self.rx.LONG_IDLE_S:
                self.rx.trigger('long_idle_return', away=self.cursor_idle)
            self.cursor_idle = 0.0; self.last_cx, self.last_cy = cx, cy
        else:
            self.cursor_idle += dt
            self.cursor_speed *= max(0.0, 1.0 - dt * 7.0)
        self.cursor_invite_in = max(0.0, self.cursor_invite_in - dt)
        self.state_t += dt
        self.cur_dx = cx - self.x
        self._think_step(dt)
        ov = self.rx.overlay()
        ba, bm_, bs_ = self.bias['affectionate'], self.bias['mischievous'], self.bias['sleepy']

        sscale = max(0.6, self.S / 0.5) ** 0.5
        # ---- movement
        if not self.dragging and self._step_mv(dt):
            pass
        elif self.dragging:
            tx = clamp(cx + self.grab[0], self.bl, self.br)
            ty = clamp(cy + self.grab[1], self.bt, self.bb)
            k = min(1.0, dt * 18.0)
            nx, ny = self.x + (tx - self.x) * k, self.y + (ty - self.y) * k
            kv = min(1.0, dt * 14.0)
            self.vx += ((nx - self.x) / dt - self.vx) * kv
            self.vy += ((ny - self.y) / dt - self.vy) * kv
            self.x, self.y = nx, ny
        else:
            st = self.state
            if st == 'idle':
                self._brake(dt, 1.6 if (abs(self.vx) + abs(self.vy)) > 200 else 2.4)
                if self.mode == 'follow':
                    self._begin('follow')
                elif (self.state_t >= self.state_dur and
                      (self.anim is None or not self.anim.playing) and not self.mv):
                    self._pick_next()
            elif st == 'wander':
                d = self._goto(self.target[0], self.target[1], dt, 135.0 * sscale)
                if d < 9.0 or self.state_t > 30:
                    self._begin('idle', random.uniform(3.0, 8.0))
            elif st == 'sprint':
                # High-speed curve, then a pronounced brake and little landing
                # compression—keeps the run readable at every pet scale.
                d = self._goto(self.target[0], self.target[1], dt, 470.0 * sscale, accel=6.6)
                if self.state_t > .78 or d < 16:
                    self._brake(dt, 5.8)
                if self.state_t >= self.state_dur:
                    self.sp_hop.kick(-95); self.sp_stretch.kick(-4); self.sp_tail.kick(-150)
                    self._begin('idle', random.uniform(3.5, 7.5))
            elif st == 'perch_go':
                pr = self._find_perch()
                if pr is None:
                    self.perch_id = None
                    self._begin('idle', 1.5)
                else:
                    tx, ty = self._perch_point(pr)
                    d = self._goto(tx, ty, dt, 130.0 * sscale)
                    if d < 8.0 or self.state_t > 25:
                        self._begin('perch', random.uniform(15, 40))
                        self.sp_stretch.kick(-5.0)
            elif st == 'perch':
                self._update_perch(dt)
            elif st == 'tosleep':
                d = self._goto(self.target[0], self.target[1], dt, 110.0 * sscale)
                if d < 5.0 or self.state_t > 40:
                    self.x, self.y = self.target
                    self.vx = self.vy = 0.0
                    self.sp_stretch.kick(-7.0)
                    self._begin('sleep', random.uniform(18, 40))
            elif st == 'sleep':
                self._brake(dt, 6.0)
                if self.state_t >= self.state_dur:
                    self._wake()
            elif st == 'wake':
                self._brake(dt, 4.0)
                if self.state_t >= self.state_dur:
                    self._begin('idle', random.uniform(2, 4))
            elif st == 'cursor_visit':
                # A voluntary visit is gentle: he watches a settled cursor, but does not chase it.
                if self.mode != 'chill' or self.state_t >= self.state_dur or self.cursor_speed > 620.0:
                    self._begin('idle', random.uniform(2.0, 4.0))
                else:
                    dx, dy = cx - self.x, cy - self.y
                    dist = math.hypot(dx, dy) or 1.0
                    keep = 132.0 * max(0.7, self.S / 0.5)
                    if dist > keep * 1.16:
                        tx, ty = cx - dx / dist * keep, cy - dy / dist * keep
                        self._goto(tx, ty, dt, 205.0 * sscale, accel=2.9)
                    else:
                        self._brake(dt, 3.2)
            elif st == 'follow':
                if self.mode != 'follow':
                    self._begin('idle', random.uniform(1.5, 3))
                else:
                    dx, dy = cx - self.x, cy - self.y
                    dist = math.hypot(dx, dy) or 1.0
                    keep = 150.0 * max(0.7, self.S / 0.5)
                    if dist > keep * 1.3:
                        tx, ty = cx - dx / dist * keep, cy - dy / dist * keep
                        self._goto(tx, ty, dt, 340.0 * sscale, accel=3.6)
                    else:
                        self._brake(dt, 2.6)
            if not self._carried:
                self.x += self.vx * dt
                self.y += self.vy * dt
            # soft walls
            if self.x < self.bl: self.x = self.bl; self.vx = abs(self.vx) * 0.3
            if self.x > self.br: self.x = self.br; self.vx = -abs(self.vx) * 0.3
            if self.y < self.bt: self.y = self.bt; self.vy = abs(self.vy) * 0.3
            if self.y > self.bb: self.y = self.bb; self.vy = -abs(self.vy) * 0.2
        if self.dragging:
            self.x = clamp(self.x, self.bl, self.br); self.y = clamp(self.y, self.bt, self.bb)

        speed = math.hypot(self.vx, self.vy)
        floor = self.bb
        if self.state == 'perch' and self.perch_rect:
            floor = self.perch_rect[1] - EXT_B * self.S + 3.0 * self.S
        height = floor - self.y
        self.ground_f = clamp(1.0 - height / 70.0, 0.0, 1.0)

        # ---- mood
        if self.excite_hold > 0:
            self.excite_hold -= dt
        else:
            self.excite = max(0.0, self.excite - dt * 0.55)
        dcur = math.hypot(cx - self.x, cy - self.y)
        near = dcur < 105.0 * max(0.7, self.S / 0.5) and not self.dragging and not self.is_sleeping()
        self.near_t = self.near_t + dt if near else 0.0
        if self.near_t > 1.5 and self.affection < 0.55:
            self.bump_affection(0.048 * dt)
        if self.near_t > 1.0:
            self.excite = max(self.excite, 0.45); self.excite_hold = max(self.excite_hold, 0.3)
        sleep_target = 1.0 if self.state == 'sleep' else (0.0 if self.state != 'tosleep' else 0.55 * self.ground_f)
        rate = 1.3 if sleep_target > self.sleep else 4.0
        self.sleep += (sleep_target - self.sleep) * min(1.0, dt * rate)
        sl = smoothstep(self.sleep)

        # ---- timed bits
        self.blink_in -= dt
        if self.blink_in <= 0 and self.blink_t < 0:
            self.blink_t = 0.0
            self.blink_in = random.uniform(2.2, 6.0) if random.random() > 0.2 else 0.18
        blink = 0.0
        if self.blink_t >= 0:
            self.blink_t += dt
            u = self.blink_t / 0.17
            if u >= 1.0:
                self.blink_t = -1.0
            else:
                blink = 0.96 * (1.0 - abs(2 * u - 1.0))
        if self.happy_t > 0:
            self.happy_t -= dt
            blink = max(blink, 0.55 * smoothstep(min(1.0, self.happy_t / 0.2)))
        blink = max(blink, sl * 1.2 if sl > 0.35 else 0.0)
        if sl > 0.35:
            blink = 1.0 if sl > 0.8 else max(0.96 * min(1.0, (sl - 0.35) / 0.45), blink if blink < 1.0 else 0.96)
        if ov and sl < 0.35:
            blink = max(blink, ov['blink'])

        self.twitch_in -= dt
        if self.twitch_in <= 0:
            self.twitch_in = random.uniform(3, 8)
            if sl < 0.5:
                (self.sp_ear_l if random.random() < 0.5 else self.sp_ear_r).kick(random.choice((-1, 1)) * 330.0)
        # Sleeping is never a frozen loop: an occasional dream tail flick, ear
        # twitch, or small sprint-like paw kick gives the curled pose life.
        self.sleep_twitch_in -= dt
        if self.sleep_twitch_in <= 0:
            self.sleep_twitch_in = random.uniform(6.0, 15.0)
            if sl > 0.72:
                dream = random.choice(('tail', 'ears', 'paw'))
                if dream == 'tail':
                    self.sp_tail.kick(random.choice((-1, 1)) * random.uniform(90, 150))
                elif dream == 'ears':
                    self.sp_ear_l.kick(random.choice((-1, 1)) * 180)
                    self.sp_ear_r.kick(random.choice((-1, 1)) * 140)
                else:
                    self.sp_hop.kick(-random.uniform(26, 48))
                    self.sp_stretch.kick(random.uniform(1.4, 2.8))
        self.lick_in -= dt
        if self.lick_in <= 0:
            self.lick_in = random.uniform(6, 14)
            if sl < 0.3 and not self.dragging:
                self.lick_t = 1.0
        if self.fun_kind:
            self.fun_t += dt
            if self.fun_t > 3.0:
                self.fun_kind = None
        self._step_character_act(dt)

        # ---- gaze
        lookx = looky = 0.0
        if self.dragging:
            lookx = clamp((cx - self.x) / 120.0, -1, 1); looky = clamp((cy - self.y) / 120.0, -1, 1)
        elif speed > 45 and self.state in ('wander', 'sprint', 'follow', 'tosleep'):
            lookx, looky = self.vx / speed * 0.9, self.vy / speed * 0.7
        elif self.mood == 'video' and self.fg and not self.is_sleeping():
            lookx = clamp(((self.fg[0] + self.fg[2]) / 2.0 - self.x) / 300.0, -1, 1)
            looky = clamp(((self.fg[1] + self.fg[3]) / 2.0 - self.y) / 300.0, -1, 1)
        elif self.fun_kind == 'look':
            lookx = math.sin(self.fun_t * 2.4) * 1.0; looky = -0.1
        elif self.fun_kind == 'investigate':
            lookx = math.sin(self.fun_t * 4.2) * .75 + .25
            looky = -.18 + math.sin(self.fun_t * 2.1) * .18
        else:
            self.glance_in -= dt
            self.gaze_wander = max(0.0, self.gaze_wander - dt)
            if self.glance_in <= 0:
                # affectionate days hold their gaze longer, mischievous days look around more (+-18 %)
                self.glance_in = random.uniform(1.2, 3.5) * (1.0 - DISP_FX * bm_ + DISP_FX * ba)
                self.glance = (random.uniform(-0.8, 0.8), random.uniform(-0.5, 0.6))
                if bm_ > 0.0 and random.random() < DISP_FX * 1.5 * bm_:
                    self.gaze_wander = random.uniform(1.0, 2.2)
            reach = 1.0 + DISP_FX * ba
            if dcur < 750 * reach and self.cursor_idle < 7.0 * reach and self.gaze_wander <= 0.0:
                g = 260.0 / reach
                lookx = clamp((cx - self.x) / g, -1, 1); looky = clamp((cy - self.y) / g, -1, 1)
            else:
                lookx, looky = self.glance
        if ov and ov['lw'] > 0.0:
            lookx = lerp(lookx, ov['lx'], ov['lw']); looky = lerp(looky, ov['ly'], ov['lw'])
        self.sp_lx.t = lookx * (1 - sl); self.sp_ly.t = looky * (1 - sl)

        # ---- springs
        self.sp_lean.t = clamp(self.vx * 0.04, -12, 12)
        self.sp_body.t = clamp(-self.vx * 0.022, -9, 9)
        self.sp_head.t = clamp(self.vx * 0.014, -5, 5) + lookx * 3.0 * (1 - sl) + 9.0 * sl
        app_ear = -5.0 if self.mood == 'game' else (-2.0 if self.mood == 'work' else (3.0 if self.mood == 'music' else 0.0))
        base_ear = 6.0 + 9.0 * self.excite - 34.0 * sl + app_ear
        flap_hz = 1.5 + speed * 0.012 + self.excite * 1.5
        flap_a = (4.0 + min(speed, 220) * 0.045 + self.excite * 4.0) * (1 - sl)
        ph = self.t * 2 * math.pi * flap_hz
        self.sp_ear_l.t = base_ear + flap_a * math.sin(ph)
        self.sp_ear_r.t = base_ear + flap_a * math.sin(ph + 0.6)
        # resting tail-curl bias: affectionate / sleepy curl in and down, mischievous holds it up (<= ~4 deg)
        tail_bias = clamp(4.3 * (0.7 * ba + bs_ - bm_), -4.3, 4.3)
        self.sp_tail.t = clamp(-self.vx * 0.03, -24, 24) + 9.0 * sl + tail_bias
        stretch_t = clamp(-self.vy * 0.0006, -0.07, 0.07) - 0.03 * sl
        if self.mood == 'music' and sl < 0.3 and not self.dragging:
            self.sp_head.t += math.sin(self.t * 2 * math.pi * 1.9) * 5.0
            stretch_t += math.sin(self.t * 2 * math.pi * 3.8) * 0.018
            self.excite = max(self.excite, 0.35); self.excite_hold = max(self.excite_hold, 0.3)
        self.sp_stretch.t = stretch_t
        self.sp_hop.t = 0.0

        tt = 0.76
        sway = 0.0
        if self.lick_t > 0:
            self.lick_t -= dt
            u = 1.0 - self.lick_t / 1.1
            tt = 1.35 + 0.1 * math.sin(u * 14)
            sway = math.sin(u * 16) * 0.9
        elif self.excite > 0.4:
            tt = 0.95; sway = math.sin(self.t * 9) * 0.5 * self.excite
        if self.dragging:
            tt = 1.15; sway = math.sin(self.t * 12) * 0.6
        tt = lerp(tt, 0.5, sl)
        self.sp_tongue.t = tt
        self.sp_tsway.t = sway

        if ov:
            self._apply_overlay(ov, sl)
        for sp in (self.sp_lean, self.sp_body, self.sp_head, self.sp_ear_l, self.sp_ear_r, self.sp_tail,
                   self.sp_stretch, self.sp_hop, self.sp_tongue, self.sp_tsway, self.sp_lx, self.sp_ly):
            sp.step(dt)

        # ---- spin / shake
        rot_extra = 0.0
        if self.spin_t >= 0.0:
            self.spin_t += dt
            u = self.spin_t / self.spin_dur
            if u >= 1.0:
                self.spin_t = -1.0
            else:
                rot_extra += self.spin_deg * smoothstep(u)
        if self.shake > 0.01:
            rot_extra += math.sin(self.t * 34.0) * self.shake * 5.0
            self.shake *= math.exp(-3.2 * dt)

        # ---- tail
        tf = lerp(2.3, 9.5, self.excite) * (1 - 0.8 * sl)
        self.tail_phase += tf * dt
        tail_amp = (lerp(0.55, 1.15, self.excite) + min(speed / 300.0, 0.4)) * (1 - 0.55 * sl)
        tail_bend = clamp(-self.vx * 0.0045 + DISP_FX * 0.8 * (ba - 0.5 * bm_), -1.0, 1.0)

        # ---- fx particles
        if sl > 0.8:
            self.z_in -= dt
            if self.z_in <= 0:
                self.z_in = 1.7
                self.fx.append(dict(kind='z', x=58, y=-150, vx=12, vy=-18, age=0.0, life=2.6, size=8.0, rot=-8))
        nfx = []
        for f in self.fx:
            f['age'] += dt
            if f['age'] < f['life']:
                f['x'] += f['vx'] * dt; f['y'] += f['vy'] * dt
                if f['kind'] in ('s', 'd'):
                    f['vy'] += 40 * dt
                nfx.append(f)
        self.fx = nfx[-16:]

        # ---- compose pose
        P = default_pose()
        S = self.S
        bob_amp = 3.4 * (1 - 0.9 * self.ground_f * (1 if self.is_sleeping() else 0.5)) * (1 - 0.7 * sl)
        # idle micro-motion: smooth sine blended with a higher-frequency jitter by disposition weight
        fmul = 1.0 + DISP_FX * (bm_ - bs_)
        self.ph_bob += 2.1 * fmul * dt
        self.ph_bob2 += 1.27 * fmul * dt
        jw = clamp(DISP_FX * 2.0 * bm_, 0.0, 0.36)
        jit1 = 0.65 * math.sin(self.ph_bob * 3.1 + 1.3) + 0.35 * math.sin(self.ph_bob2 * 5.3 + 0.4)
        jit2 = 0.65 * math.sin(self.ph_bob2 * 3.7 + 0.2) + 0.35 * math.sin(self.ph_bob * 4.9 + 2.1)
        jith = 0.65 * math.sin(self.ph_bob * 2.9 + 2.0) + 0.35 * math.sin(self.ph_bob2 * 4.7 + 0.9)
        bob = (lerp(math.sin(self.ph_bob), jit1, jw) * bob_amp
               + lerp(math.sin(self.ph_bob2 + 1.0), jit2, jw) * bob_amp * 0.4)
        wx = math.floor(self.x - self.W / 2.0)
        wy = math.floor(self.y - self.H / 2.0)
        self.win = (int(wx), int(wy))
        stretch = self.sp_stretch.x
        P['ox'] = (self.x - self.W / 2.0) - wx
        P['oy'] = (self.y - self.H / 2.0) - wy + (bob + self.sp_hop.x + 5.0 * sl) * S
        P['rot'] = self.sp_lean.x + rot_extra
        P['sy'] = 1.0 + stretch
        P['sx'] = 1.0 - stretch * 0.65
        P['head_rot'] = self.sp_head.x
        P['head_dy'] = 3.0 * sl + 1.2 * lerp(math.sin(self.ph_bob + 0.5), jith, jw) * (1 - sl)
        P['body_rot'] = self.sp_body.x
        P['breathe'] = (0.008 * math.sin(self.t * 1.9) * (1 - sl) + 0.02 * math.sin(self.t * 1.15) * sl)
        P['ear_l'] = self.sp_ear_l.x
        P['ear_r'] = self.sp_ear_r.x
        P['tail_amp'] = tail_amp
        P['tail_phase'] = self.tail_phase
        P['tail_bend'] = tail_bend
        P['tail_rot'] = self.sp_tail.x
        P['look_x'] = self.sp_lx.x
        P['look_y'] = self.sp_ly.x
        P['blink'] = blink
        P['tongue'] = clamp(self.sp_tongue.x, 0.3, 1.6)
        P['tongue_sway'] = self.sp_tsway.x
        P['shadow'] = (self.ground_f ** 1.5) * 0.9
        P['affection'] = self.affection
        fx = []
        for f in self.fx:
            u = f['age'] / f['life']
            alpha = min(1.0, u * 6.0) * (1.0 - smoothstep((u - 0.55) / 0.45))
            size = f['size'] * (1.0 + 0.5 * u) if f['kind'] == 'z' else f['size'] * (1.0 - 0.4 * u)
            fx.append((f['kind'], f['x'], f['y'], size, alpha, f['rot'] + u * 12))
        P['fx'] = fx
        P = self._apply_character_act_pose(P)
        # Additive animation pack goes last: offsets layer over breathing, gaze,
        # reactions and character acts, and never replace the pose underneath.
        if (self.anim is not None and self.anim.active and
                self.anim_enabled and not self.anim_failed):
            try:
                n_own = len(P['fx'])
                self.anim.update(dt)
                self.anim.apply(P, S)
                if len(P['fx']) > n_own:
                    # The pack's own sparks and sleep Zs use the demo's white
                    # halo; tag just those particles so host reaction sparkles
                    # and the host sleep Z keep their existing look.
                    tail = [('p' + t[0],) + t[1:] if t[0] in ('s', 'z') else t
                            for t in P['fx'][n_own:]]
                    P['fx'] = P['fx'][:n_own] + tail
                for rq in self.anim.take_requests():
                    self._anim_request(rq)
            except Exception as exc:
                # Fail safe: one bad clip must never take the pet down.  Drop the
                # layer for this session (the base pose is still intact) and say
                # so once, instead of printing on every frame.
                self.anim_failed = True
                try:
                    self.anim.stop(0.1); self.anim.clip = None
                    self.anim.springs.clear(); self.anim.particles = []
                    self.anim.requests = []
                except Exception:
                    pass
                print('[chill-pet] animation pack disabled after an error: %r' % (exc,))
        self.pose = P
        return P

    def window_pos(self):
        return self.win


# ============================================================================
#  Desktop application layer (Qt)
# ============================================================================
import sys, os, time, json, math, subprocess, argparse
from importlib import import_module

SETTINGS_PATH = os.path.join(os.path.expanduser('~'), '.chill_pet_settings.json')
SIZES = [('Tiny', 0.35), ('Small', 0.5), ('Medium', 0.7), ('Large', 1.0), ('Huge', 1.4)]

def load_settings():
    try:
        with open(SETTINGS_PATH, 'r') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def save_settings(d):
    try:
        with open(SETTINGS_PATH, 'w') as f:
            json.dump(d, f)
    except Exception:
        pass

def import_qt(auto=True):
    for name in ('PySide6', 'PyQt6'):
        try:
            return (import_module(name + '.QtCore'), import_module(name + '.QtGui'),
                    import_module(name + '.QtWidgets'))
        except ImportError:
            continue
    if auto:
        print('Qt not found - installing PySide6 (one time, takes a minute)...')
        for extra in ([], ['--user']):
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'PySide6'] + extra)
                break
            except Exception:
                continue
        return import_qt(False)
    return None


# ============================================================================
#  Window awareness (Windows only, light: polled twice a second)
#  Only window RECTANGLES and TITLES are read, locally, and never stored/sent.
# ============================================================================
SHELL_CLASSES = ('Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd',
                 'Windows.UI.Core.CoreWindow', 'NotifyIconOverflowWindow')

MOODS = (
    ('music', ('spotify', 'soundcloud', 'deezer', 'youtube music', 'apple music', 'tidal', 'winamp', 'foobar', 'music')),
    ('video', ('youtube', 'netflix', 'twitch', 'vlc', 'prime video', 'disney+', 'crunchyroll', 'hulu', 'plex', 'mpv', 'video')),
    ('game',  ('steam', 'epic games', 'xbox', 'playstation', 'minecraft', 'fortnite', 'valorant', 'league of legends', 'game')),
    ('work',  ('visual studio', 'vs code', 'pycharm', 'intellij', 'sublime', 'notepad++', 'github', 'terminal',
               'powershell', 'command prompt', 'word', 'excel', 'powerpoint', 'docs', 'sheets', 'notion', 'figma', 'outlook')),
)

def mood_from_title(title):
    t = (title or '').lower()
    for name, words in MOODS:
        for w in words:
            if w in t:
                return name
    return None

def filter_windows(raw, own=None, min_w=200, min_h=120):
    """raw: z-ordered (top first) list of dicts: id, rect(l,t,r,b), title, cls, tool(bool), cloaked(bool).
    Returns visible, perch-able windows whose title strip is not covered by a higher window."""
    out, seen = [], []
    for w in raw:
        if w['id'] == own or w.get('cloaked') or w.get('cls') in SHELL_CLASSES:
            continue
        l, t, r, b = w['rect']
        if r - l < min_w or b - t < min_h:
            continue
        covered = False
        for sx in (0.3, 0.5, 0.7):
            px, py = l + (r - l) * sx, t + 3
            for (a, bb, c, d) in seen:
                if a <= px <= c and bb <= py <= d:
                    covered = True
                    break
            if covered:
                break
        seen.append((l, t, r, b))
        if covered or w.get('tool') or not w.get('title'):
            continue
        out.append(dict(id=w['id'], rect=(l, t, r, b), title=w['title']))
    return out

class WinWatcher(object):
    def __init__(self):
        self.ok = False
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            from ctypes import wintypes
            self.ct = ctypes
            self.u = ctypes.windll.user32
            self.d = ctypes.windll.dwmapi
            u, d = self.u, self.d
            H = wintypes.HWND
            self.RECT = wintypes.RECT
            self.PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, H, wintypes.LPARAM)
            u.EnumWindows.argtypes = [self.PROC, wintypes.LPARAM]
            u.GetForegroundWindow.restype = H
            for n in ('IsWindowVisible', 'IsIconic', 'IsWindow'):
                getattr(u, n).argtypes = [H]
            u.GetWindowRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
            u.GetWindowTextW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
            u.GetClassNameW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
            u.GetWindowLongW.argtypes = [H, ctypes.c_int]
            d.DwmGetWindowAttribute.argtypes = [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            self.ok = True
        except Exception:
            self.ok = False

    def _rect(self, h):
        r = self.RECT()
        ct = self.ct
        if self.d.DwmGetWindowAttribute(h, 9, ct.byref(r), ct.sizeof(r)) != 0:
            self.u.GetWindowRect(h, ct.byref(r))
        return (r.left, r.top, r.right, r.bottom)

    def _text(self, fn, h):
        buf = self.ct.create_unicode_buffer(256)
        fn(h, buf, 256)
        return buf.value

    def _info(self, h):
        ct, u = self.ct, self.u
        ex = u.GetWindowLongW(h, -20)
        c = ct.c_int(0)
        self.d.DwmGetWindowAttribute(h, 14, ct.byref(c), 4)
        return dict(id=int(h), rect=self._rect(h), title=self._text(u.GetWindowTextW, h),
                    cls=self._text(u.GetClassNameW, h),
                    tool=bool(ex & 0x80) and not (ex & 0x40000), cloaked=bool(c.value))

    def snapshot(self, own=None):
        """-> (raw_windows_in_z_order, foreground_info_or_None)  [physical pixels]"""
        if not self.ok:
            return [], None
        raw = []
        u = self.u
        def cb(h, _):
            try:
                if u.IsWindowVisible(h) and not u.IsIconic(h):
                    raw.append(self._info(h))
            except Exception:
                pass
            return True
        keep = self.PROC(cb)
        u.EnumWindows(keep, 0)
        fg = None
        try:
            h = u.GetForegroundWindow()
            if h and int(h) != own:
                fg = self._info(h)
        except Exception:
            fg = None
        return raw, fg

    def rect_of(self, hid):
        """live rect of one window, or None if gone / minimised / hidden"""
        if not self.ok:
            return None
        try:
            h = self.ct.c_void_p(hid)
            if not self.u.IsWindow(h) or not self.u.IsWindowVisible(h) or self.u.IsIconic(h):
                return None
            return self._rect(h)
        except Exception:
            return None


def build(QtCore, QtGui, QtWidgets):
    Qt = QtCore.Qt
    # Some portable/offscreen Qt runtimes expose no system font database. Load
    # Windows' normal UI typeface explicitly when present so speech is always
    # readable rather than becoming tofu squares.
    try:
        font_path = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'segoeui.ttf')
        if os.path.isfile(font_path):
            QtGui.QFontDatabase.addApplicationFont(font_path)
    except Exception:
        pass

    class QtCanvas(object):
        def __init__(self, painter):
            self.p = painter
            self._col = {}
            self._brush = {}

        def _c(self, c):
            q = self._col.get(c)
            if q is None:
                q = QtGui.QColor(c[0], c[1], c[2], c[3])
                self._col[c] = q
            return q

        @staticmethod
        def _path(path):
            qp = QtGui.QPainterPath()
            for c in path:
                k = c[0]
                if k == 'M':
                    qp.moveTo(c[1], c[2])
                elif k == 'L':
                    qp.lineTo(c[1], c[2])
                elif k == 'C':
                    qp.cubicTo(c[1], c[2], c[3], c[4], c[5], c[6])
                else:
                    qp.closeSubpath()
            return qp

        def fill(self, path, color):
            b = self._brush.get(color)
            if b is None:
                b = QtGui.QBrush(self._c(color))
                self._brush[color] = b
            self.p.fillPath(self._path(path), b)

        def stroke(self, path, color, width):
            pen = QtGui.QPen(self._c(color))
            pen.setWidthF(max(0.25, width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            self.p.strokePath(self._path(path), pen)

    class ThoughtBubble(QtWidgets.QWidget):
        """A proper cartoon speech balloon, independently fading above the pet."""
        def __init__(self):
            super(ThoughtBubble, self).__init__()
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                                Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.label = QtWidgets.QLabel(self)
            self.label.setWordWrap(True)
            self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.label.setFont(QtGui.QFont('Segoe UI', 10))
            self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.label.setStyleSheet('QLabel { color: #151518; background: transparent; border: none; '
                                     'font-weight: 600; padding: 0px; }')
            self._status = False
            self._style = 'round'
            self.fade = QtCore.QPropertyAnimation(self, b'windowOpacity', self)
            self.fade.setDuration(560)
            self.fade.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            self._fading_out = False
            self.fade.finished.connect(self._on_fade_finished)
            self.life = QtCore.QTimer(self)
            self.life.setSingleShot(True)
            self.life.timeout.connect(self._hide)

        def _place(self, anchor):
            target_x = int(anchor.x() - self.width() / 2)
            target_y = int(anchor.y() - self.height() - 16)
            screen = QtGui.QGuiApplication.screenAt(anchor) or QtGui.QGuiApplication.primaryScreen()
            g = screen.availableGeometry()
            target_x = max(g.x() + 5, min(target_x, g.right() - self.width() - 5))
            target_y = max(g.y() + 5, target_y)
            self.move(target_x, target_y)

        def show_text(self, text, anchor, status=False, style=None):
            self.fade.stop(); self.life.stop(); self._fading_out = False
            self._status = bool(status)
            self._style = style or ('status' if status else 'round')
            self.label.setText(str(text)[:180])
            self.label.setMaximumWidth(250)
            self.label.adjustSize()
            body_w = max(128, min(270, self.label.width() + 26))
            body_h = max(44, self.label.height() + 23)
            self.resize(body_w, body_h + 18)  # room for the pointed balloon tail
            self.label.setGeometry(13, 10, body_w - 26, body_h - 17)
            self._place(anchor)
            self.setWindowOpacity(0.0); self.show()
            self.fade.setStartValue(0.0); self.fade.setEndValue(1.0); self.fade.start()
            self.life.start(5600 if not status else 2800)

        def paintEvent(self, event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            body = QtCore.QRectF(2, 2, self.width() - 4, self.height() - 20)
            path = QtGui.QPainterPath()
            action = self._style
            if action in ('startled_hop', 'annoyed_wiggle'):
                # Comic burst: sharp, uneven rays convey a real startled/huffy beat.
                cx, cy = body.center().x(), body.center().y()
                pts = []
                for i in range(18):
                    a = math.pi * 2 * i / 18.0
                    r = (body.width() * .52 if i % 2 == 0 else body.width() * .40)
                    ry = r * body.height() / body.width()
                    pts.append(QtCore.QPointF(cx + math.cos(a) * r, cy + math.sin(a) * ry))
                path.moveTo(pts[0])
                for pt in pts[1:]: path.lineTo(pt)
                path.closeSubpath()
            elif action in ('affectionate_nuzzle', 'sleepy_settle'):
                # Cloud balloon for soft/sleepy feelings.
                circles = ((.22,.55,.22),(.40,.32,.25),(.62,.29,.25),(.79,.54,.22),(.62,.73,.25),(.35,.73,.25))
                for rx, ry, rr in circles:
                    puff = QtGui.QPainterPath()
                    puff.addEllipse(QtCore.QPointF(body.left()+body.width()*rx, body.top()+body.height()*ry),
                                    body.width()*rr, body.height()*rr)
                    path = puff if path.isEmpty() else path.united(puff)
            elif action in ('curious_perk', 'redirected_blink'):
                path.addEllipse(body)
            else:
                path.addRoundedRect(body, 18, 18)
            tail_x = self.width() * 0.5
            # A gently curved triangular tail makes this a speech bubble rather
            # than a rounded rectangle.  It points exactly toward the pet.
            path.moveTo(tail_x - 12, body.bottom() - 2)
            path.quadTo(tail_x - 4, body.bottom() + 9, tail_x, body.bottom() + 16)
            path.quadTo(tail_x + 5, body.bottom() + 9, tail_x + 13, body.bottom() - 2)
            path.closeSubpath()
            p.setBrush(QtGui.QColor(255, 250, 238, 244 if not self._status else 232))
            pen = QtGui.QPen(QtGui.QColor(21, 21, 24, 242), 2.2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin); p.setPen(pen); p.drawPath(path)
            # A tiny offset halftone shadow borrows the comic language from the
            # supplied reference without covering text or adding image assets.
            p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(21, 21, 24, 105))
            for row in range(3):
                for col in range(7):
                    x = self.width() - 22 + col * 3.5 - row * 2.0
                    y = self.height() - 17 + row * 3.6
                    p.drawEllipse(QtCore.QPointF(x, y), .85, .85)
            # Small status face/dots keeps even short lines visually intentional.
            p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(21, 21, 24, 170))
            if self._status:
                for x in (15, 21, 27): p.drawEllipse(QtCore.QPointF(x, 14), 1.65, 1.65)
            else:
                p.drawEllipse(QtCore.QPointF(16, 14), 2.0, 2.0)
                p.drawEllipse(QtCore.QPointF(24, 14), 2.0, 2.0)
            p.end()

        def follow(self, anchor):
            if self.isVisible() and not self._fading_out:
                self._place(anchor)

        def _hide(self):
            if self._fading_out or not self.isVisible():
                return
            self._fading_out = True
            self.fade.stop(); self.fade.setStartValue(self.windowOpacity()); self.fade.setEndValue(0.0)
            self.fade.start()

        def _on_fade_finished(self):
            if self._fading_out:
                self.hide()
                self._fading_out = False

    class PetWindow(QtWidgets.QWidget):
        def __init__(self, settings, args):
            super(PetWindow, self).__init__()
            self.settings = settings
            self.setWindowTitle('Chill Pet')
            flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                     Qt.WindowType.Tool)
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            try:
                self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
            except Exception:
                pass
            self.setCursor(Qt.CursorShape.OpenHandCursor)

            self.S = float(settings.get('scale', 0.5))
            self.W = int(round(DESIGN_W * self.S)); self.H = int(round(DESIGN_H * self.S))
            self._pad = 0.0
            self.setFixedSize(self.W, self.H)
            mode = settings.get('mode', 'chill')
            self.brain = Brain(self.W, self.H, self.S, mode=mode, sleepy=settings.get('sleepy', True))
            self.brain.cursor_curiosity = bool(settings.get('cursor_curiosity', True))
            self.brain.motion_scale = 0.48 if settings.get('reduced_motion', False) else 1.0
            self.brain.energy_level = max(.25, min(1.0, float(settings.get('energy_level', .65))))
            self.brain.weather_mood = settings.get('weather_mood', 'off')
            self.brain.anim_enabled = bool(settings.get('animation_pack_enabled', True))
            self.brain.anim_travel = bool(settings.get('animation_travel_enabled', False))
            self.model_path = getattr(args, 'model', None) or default_model_path()
            self.pet_state = PetState()
            self.thought_engine = ThoughtEngine(lambda: LlamaBackend(self.model_path),
                                                debug=bool(getattr(args, 'llm_debug', False)))
            self.brain.rx.state = self.pet_state
            self.brain.rx.engine = self.thought_engine
            self.thinking = False
            self.sound_on = bool(settings.get('sound', True))
            self.sound_volume = max(0, min(100, int(settings.get('sound_volume', 65))))
            self.sound_bank = SoundBank(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds'))
            self.bridge_enabled = bool(settings.get('companion_bridge', False))
            self.bridge = CompanionBridge()
            self.activity_log = []
            self.icon_mover = DesktopIconMover()
            self.icon_moves_enabled = bool(settings.get('desktop_icon_moves', False)) and self.icon_mover.ok
            legacy_auto = bool(settings.get('desktop_icon_autonomy', False))
            self.icon_control_mode = settings.get('desktop_icon_control', 'auto' if legacy_auto else 'manual')
            if self.icon_control_mode not in ('manual', 'assisted', 'auto'): self.icon_control_mode = 'manual'
            if not self.icon_moves_enabled: self.icon_control_mode = 'manual'
            self.icon_autonomy_enabled = self.icon_control_mode == 'auto'
            self.brain.allow_icon_autonomy = self.icon_control_mode != 'manual'
            self._next_auto_icon_at = 0.0
            self._icon_request_until = 0.0
            self._carry_release_until = 0.0
            self._icon_job = None
            self.cursor_tug_enabled = bool(settings.get('cursor_tug_game', False))
            self._cursor_tug = None
            self._next_cursor_tug_at = time.monotonic() + random.uniform(18.0, 35.0)
            if self.bridge_enabled and not self.bridge.start():
                self.bridge_enabled = False
            self.setup = None
            self.bubble = ThoughtBubble()
            self.pose = default_pose()
            self._press = None
            self._dragging = False
            self._wpos = None
            self._last = time.perf_counter()

            scr = QtGui.QGuiApplication.primaryScreen()
            g = scr.availableGeometry()
            self.brain.place(g.x() + g.width() - 230 * self.S - 60, g.y() + g.height() - EXT_B * self.S)
            self.brain.update(0.016, (0, 0), (g.x(), g.y(), g.x() + g.width(), g.y() + g.height()))
            wx, wy = self.brain.window_pos()
            self.move(wx, wy)

            self.watcher = WinWatcher()
            self.react = bool(settings.get('react', True)) and self.watcher.ok
            # Always visible by default, including borderless/fullscreen applications.
            self.show_fullscreen = bool(settings.get('show_fullscreen', True))
            self.hide_fs = (not self.show_fullscreen) and self.watcher.ok
            self._wins, self._mood, self._fg = [], None, None
            self._fg_title = None
            self._poll_t = 0.0
            self.hidden = False
            self.timer = QtCore.QTimer(self)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.timer.timeout.connect(self.tick)
            self.fps60 = bool(settings.get('fps60', True))
            self.timer.start(16 if self.fps60 else 33)
            self._make_tray()
            if bool(settings.get('think', False) or getattr(args, 'think', False)):
                self.set_thinking(True)

        # ---------------------------------------------------------- loop
        def _bounds(self):
            b = self.brain
            app = QtGui.QGuiApplication
            if b.dragging:
                l = t = 10 ** 9; r = bt = -10 ** 9
                for s in app.screens():
                    g = s.availableGeometry()
                    l = min(l, g.x()); t = min(t, g.y())
                    r = max(r, g.x() + g.width()); bt = max(bt, g.y() + g.height())
                return (l, t, r, bt)
            scr = app.screenAt(QtCore.QPoint(int(b.x), int(b.y))) or app.primaryScreen()
            g = scr.availableGeometry()
            return (g.x(), g.y(), g.x() + g.width(), g.y() + g.height())

        def _fit_animation_headroom(self):
            """Grow/shrink the window symmetrically while a pack clip plays.

            The pet is always drawn at the window centre, so adding equal
            transparent padding never moves the character on screen.  The
            behavior bounds subtract this padding again, which keeps the pet
            from teleporting or refusing to walk near a screen edge just
            because the transparent click area got bigger for a moment.
            """
            pad = float(self.brain.animation_pad())
            if abs(pad - self._pad) < 0.5:
                return
            self._pad = pad
            self.W = int(round((DESIGN_W + 2.0 * pad) * self.S))
            self.H = int(round((DESIGN_H + 2.0 * pad) * self.S))
            self.setFixedSize(self.W, self.H)
            self.brain.set_size(self.W, self.H, self.S)
            self.brain.view_pad = pad
            # Move in the same call as the resize.  setFixedSize keeps the
            # top-left anchored; re-centring here means the compositor never
            # gets a chance to paint the character 80 px off for one frame.
            wx = int(math.floor(self.brain.x - self.W / 2.0))
            wy = int(math.floor(self.brain.y - self.H / 2.0))
            self.move(wx, wy)
            self._wpos = (wx, wy)

        def _sync_fps(self):
            """Never let a playing pack clip drop to the 30 fps idle cadence.

            The normal toggle still controls the resting pet, but the 16 pack
            clips are authored for 60 fps and must stay smooth even when the
            saved preference is 30 fps.
            """
            animating = self.brain.anim is not None and self.brain.anim.active
            interval = 16 if (self.fps60 or animating) else 33
            if self.timer.interval() != interval:
                self.timer.setInterval(interval)

        def play_animation(self, name):
            """Menu entry point: start the clip and raise the timer immediately."""
            if self.brain.play_anim(name, force=True):
                self._sync_fps()

        def tick(self):
            now = time.perf_counter()
            dt = now - self._last
            self._last = now
            self._fit_animation_headroom()
            self._sync_fps()
            self._advance_icon_carry(now)
            cp = QtGui.QCursor.pos()
            self._advance_cursor_tug(now, cp)
            cp = QtGui.QCursor.pos()  # may have shifted a few pixels during tug
            perch_rect = None
            if self.react or self.hide_fs:
                self._poll_t += dt
                if self._poll_t >= 0.5:
                    self._poll_t = 0.0
                    self._poll_windows()
                if self.hidden:
                    return
                bid = self.brain.perch_id
                if self.react and bid is not None and self.brain.state == 'perch':
                    perch_rect = self._logical(self.watcher.rect_of(bid))
            # A slow frame is a hint that flashy clips should wait; the flag
            # only gates new clips, it never cuts one already playing.
            self.brain.low_fps = dt > 0.055
            pose = self.brain.update(dt, (cp.x(), cp.y()), self._bounds(),
                                     self._wins if self.react else None,
                                     self._mood if self.react else None,
                                     self._fg if self.react else None, perch_rect)
            if pose is None:
                return
            if self.bridge_enabled:
                for event in self.bridge.drain():
                    self.brain.companion_event(event)
                    self.activity_log.append(dict(t=datetime.datetime.now().strftime('%H:%M:%S'),
                                                  source=event['source'], event=event['event']))
                    self.activity_log = self.activity_log[-100:]
            self.pose = self._apply_icon_carry_pose(pose, now)
            self.pose = self._apply_cursor_tug_pose(self.pose, now)
            self._handle_reaction_events()
            self._poll_setup()
            wp = self.brain.window_pos()
            if wp != self._wpos:
                self.move(wp[0], wp[1])
                self._wpos = wp
            self.bubble.follow(self._anchor())
            self.update()

        def _advance_icon_carry(self, now):
            job = self._icon_job
            if job is None: return
            u = min(1.0, max(0.0, (now - job['started']) / job['duration']))
            b = self.brain
            # Three readable phases: pet approaches the actual icon first,
            # ears grip it, then both travel together along the same curved
            # path, followed by an independent release/settle.
            approach = .27
            x0,y0 = job['start']; x1,y1 = job['end']
            offx, offy = job['offset']
            if u < approach:
                q = u / approach; e = q*q*(3.0-2.0*q)
                px = job['pet_start'][0] + ((x0+offx)-job['pet_start'][0])*e
                py = job['pet_start'][1] + ((y0+offy)-job['pet_start'][1])*e
            else:
                q = min(1.0, (u-approach)/(1.0-approach-.14)); e = q*q*(3.0-2.0*q)
                x = x0 + (x1-x0)*e
                y = y0 + (y1-y0)*e - math.sin(math.pi*q)*42
                self.icon_mover.set_position(job['hwnd'], job['index'], x, y)
                px, py = x+offx, y+offy-10*math.sin(math.pi*q)
            oldx, oldy = b.x, b.y
            b.x, b.y = px, py; b.vx = (px-oldx)*35; b.vy = (py-oldy)*35
            b.target = (px, py)
            if u >= approach and not job['grabbed']:
                job['grabbed'] = True
                self.brain.sp_ear_l.kick(440); self.brain.sp_ear_r.kick(370)
                self.brain.sp_head.kick(110); self.brain.sp_lean.kick(125); self.brain.sp_tail.kick(190)
                self.brain._emit_fx('q'); self._play_sound('grab')
            elif u > .86 and not job['released']:
                job['released'] = True
                self.brain.sp_hop.kick(-110); self.brain.sp_stretch.kick(-5); self.brain.sp_tail.kick(-175)
                self.brain._emit_fx('s'); self.brain._emit_fx('h'); self._play_sound('land')
            if u >= 1.0:
                self.icon_mover.set_position(job['hwnd'], job['index'], *job['end'])
                b._begin('idle', random.uniform(2.5, 4.5))
                self.activity_log.append(dict(t=datetime.datetime.now().strftime('%H:%M:%S'),source='desktop',event='animated icon carry'))
                self._icon_job = None
                self._carry_release_until = now + .42
                self.timer.setInterval(16 if self.fps60 else 33)
                self.bubble.show_text('Delivered!', self._anchor(), status=True)

        def _apply_icon_carry_pose(self, pose, now):
            """Continuous ears-first grab, balance-carry, and release acting."""
            job = self._icon_job
            if job is None:
                if now < self._carry_release_until:
                    v = (self._carry_release_until - now) / .42
                    pose['sy'] -= .055*v; pose['sx'] += .040*v
                    pose['ear_l'] += 85*v; pose['ear_r'] += 70*v; pose['tail_rot'] -= 95*v
                    pose['fx'].append(('s', 55, -110, 9, v, 0))
                return pose
            u = min(1.0, max(0.0, (now-job['started']) / job['duration']))
            if u < .27:                 # approach/crouch
                q = smoothstep(u/.27)
                pose['sy'] -= .075*q; pose['sx'] += .050*q; pose['head_rot'] += 18*q
                pose['ear_l'] += 25*q; pose['ear_r'] += 25*q; pose['tail_rot'] -= 55*q; pose['look_x'] = .95
            elif u < .43:               # ears close and tug
                q = smoothstep((u-.27)/.16)
                pose['sy'] -= .045; pose['sx'] += .030; pose['head_rot'] += 25*q
                pose['ear_l'] += 48*q; pose['ear_r'] += 46*q; pose['tail_rot'] += 90*q; pose['look_x'] = .85
                pose['fx'].append(('q', 62, -130, 8, 1.0-q*.25, -10))
            elif u < .86:               # held carry, tail counter-steering
                q = (u-.43)/.43; sway = math.sin(q*math.pi*3.0)
                pose['body_rot'] += 7*sway; pose['head_rot'] += 11+5*sway
                pose['ear_l'] += 32; pose['ear_r'] += 30; pose['tail_rot'] += -130*sway
                pose['tail_amp'] *= .55; pose['sy'] -= .025; pose['sx'] += .018
                pose['look_x'] = .65; pose['look_y'] = -.18
            else:                        # let go and recover
                q = smoothstep((u-.86)/.14)
                pose['ear_l'] += 48*(1-q); pose['ear_r'] += 45*(1-q)
                pose['head_rot'] -= 22*q; pose['tail_rot'] -= 120*q
                pose['sy'] -= .05*(1-q); pose['sx'] += .035*(1-q)
                pose['fx'].append(('s', 50, -116, 9, .8, 0))
            return pose

        def _escape_down(self):
            try:
                return bool(self.icon_mover.u.GetAsyncKeyState(0x1B) & 0x8000)
            except Exception:
                return False

        def _start_cursor_tug(self, now, cursor):
            b = self.brain
            # Never begin over immersive media/game contexts or during any drag.
            if (not self.cursor_tug_enabled or b.dragging or b.is_sleeping() or
                    b.mood in ('video','game') or b.mode == 'follow' or
                    b.mv or b.state == 'icon_carry'):
                return False
            d = math.hypot(cursor.x()-b.x, cursor.y()-b.y)
            if d > 210*self.S or b.cursor_idle < 1.0:
                return False
            if b.anim is not None:
                b.anim.stop(0.12)
            self._cursor_tug = dict(started=now, cursor=(cursor.x(),cursor.y()),
                                    last=(cursor.x(),cursor.y()), resistance=0.0, gave_up=False)
            b.sp_ear_l.kick(390); b.sp_ear_r.kick(360); b.sp_head.kick(95); b.sp_tail.kick(180)
            b._emit_fx('q'); self._play_sound('grab')
            return True

        def _advance_cursor_tug(self, now, cursor):
            t = self._cursor_tug
            if t is None:
                if now >= self._next_cursor_tug_at:
                    self._next_cursor_tug_at = now + random.uniform(30.0, 65.0)
                    self._start_cursor_tug(now, cursor)
                return
            age = now - t['started']
            if self._escape_down() or not self.cursor_tug_enabled or age > 2.35:
                self._cursor_tug = None; self._next_cursor_tug_at = now + random.uniform(30,60); return
            b = self.brain
            # User can resist naturally: a large deviation from the previous
            # proposed point makes the pet strain, then gives up rather than
            # trapping the cursor.
            px,py = t['last']; user_move = math.hypot(cursor.x()-px,cursor.y()-py)
            if user_move > 18:
                t['resistance'] = min(1.0, t['resistance'] + (user_move-18)/55.0)
                b.sp_tail.kick(random.choice((-1,1))*120); b.sp_head.kick(-80); b.shake=max(b.shake,.35)
            else:
                t['resistance'] = max(0.0,t['resistance']-.025)
            if t['resistance'] > .88:
                b._emit_fx('d'); self._play_sound('land'); self._cursor_tug=None
                self._next_cursor_tug_at=now+random.uniform(35,70); return
            # A small per-frame pull toward the pet; user input always wins once
            # it meaningfully resists. It is a game, not mouse capture.
            dx,dy = b.x-cursor.x(),b.y-cursor.y(); dist=math.hypot(dx,dy) or 1.0
            pull = 3.5 * (1.0-t['resistance'])
            gx,gy = cursor.x()+dx/dist*pull, cursor.y()+dy/dist*pull
            QtGui.QCursor.setPos(int(gx),int(gy)); t['last']=(gx,gy)
            if age > .42:
                b.sp_lean.kick((1 if dx>0 else -1)*12); b.sp_tail.kick(math.sin(age*14)*18)

        def _apply_cursor_tug_pose(self, pose, now):
            t=self._cursor_tug
            if t is None: return pose
            age=now-t['started']; resist=t['resistance']; wave=math.sin(age*15)
            pose['ear_l'] += 38+10*wave; pose['ear_r'] += 35-10*wave
            pose['head_rot'] += 18-20*resist; pose['tail_rot'] += 100*wave-70*resist
            pose['sy'] -= .035; pose['sx'] += .024; pose['look_x']=.9; pose['look_y']=-.1
            pose['fx'].append(('q' if resist<.4 else 'd', 52, -125, 8, .75, 0))
            return pose

        def _anchor(self):
            # Keep the bubble at the same screen point whether or not the
            # transparent animation headroom is currently added.  The base
            # window places this point at 27% of its height, i.e. 101 design
            # px above the character/window centre.
            return self.mapToGlobal(QtCore.QPoint(
                self.width() // 2, int(self.height() / 2 - 101 * self.S)))

        def _handle_reaction_events(self):
            sounds = []
            for kind, payload in self.brain.drain_events():
                if kind == 'bubble' and payload:
                    if isinstance(payload, tuple):
                        self.bubble.show_text(payload[0], self._anchor(), style=payload[1])
                    else:
                        self.bubble.show_text(payload, self._anchor())
                    sounds.append('talk')
                elif kind == 'sound':
                    sounds.append(payload)
                elif kind == 'icon_candidate':
                    if (self.icon_control_mode == 'auto' and self.icon_moves_enabled and
                            time.monotonic() >= self._next_auto_icon_at and self._icon_job is None):
                        self._start_icon_carry(autonomous=True)
                    elif (self.icon_control_mode == 'assisted' and self.icon_moves_enabled and
                          self._icon_job is None and time.monotonic() >= self._next_auto_icon_at):
                        self._icon_request_until = time.monotonic() + 18.0
                        self.bubble.show_text('Can I carry one desktop icon?', self._anchor(), status=True)
            # One light clip per frame.  A movement/reaction cue takes priority
            # over the accompanying spoken meow, so clips are never cut off.
            if self.sound_on and sounds:
                self._play_sound(next((x for x in sounds if x != 'talk'), sounds[0]))

        def _play_sound(self, kind):
            try:
                kind = ANIM_SOUND_ALIAS.get(kind, kind)
                import winsound
                clip = self.sound_bank.pick(kind)
                if clip:
                    # The clips are extracted once with tiny edge fades.  This
                    # leaves their interior sound untouched and prevents clicks.
                    path = self.sound_bank.playable_path(clip, self.sound_volume)
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC |
                                       winsound.SND_NODEFAULT)
                    return
                bias = self.brain.bias
                pitch = 1.0 + 0.10 * bias['mischievous'] - 0.07 * bias['sleepy']
                raw = synth_wav(kind, pitch=pitch, bright=0.28 + 0.22 * bias['mischievous'],
                                soft=max(0.0, bias['sleepy']))
                # Procedural fallback gets the same persisted master volume.
                path = self.sound_bank.playable_raw_path('fallback-' + str(kind), raw, self.sound_volume)
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass

        def _poll_setup(self):
            if self.setup is None:
                return
            if not self.setup.done:
                if self.tray: self.tray.setToolTip('Chill Pet — ' + self.setup.msg)
                return
            job, self.setup = self.setup, None
            if job.ok:
                self.brain.rx.set_enabled(True)
                label = model_label(self.model_path)
                if self.tray: self.tray.setToolTip('Chill Pet — thinking enabled (%s)' % label)
                self.bubble.show_text('%s is ready.' % label, self._anchor(), status=True)
            else:
                self.thinking = False
                self.brain.rx.set_enabled(False)
                if self.tray: self.tray.setToolTip('Chill Pet — thinking unavailable')
                self.bubble.show_text('Thinking needs local setup — see README.', self._anchor(), status=True)

        def set_thinking(self, on):
            self.thinking = bool(on)
            if not on:
                self.brain.rx.set_enabled(False)
                if self.setup is not None: self.setup = None
                self._save(); return
            # Runtime probing can invoke a child Python process, so it deliberately
            # stays on the setup thread even when the model already exists.
            self.brain.rx.set_enabled(False)
            self.setup = BrainSetup(self.model_path)
            self.setup.start()
            self._save()

        def paintEvent(self, ev):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 0))
            p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
            draw_pet(QtCanvas(p), self.pose, self.W, self.H, self.S)
            p.end()

        # ---------------------------------------------------------- window awareness
        def _logical(self, r):
            if r is None:
                return None
            k = self.devicePixelRatioF() or 1.0
            return (r[0] / k, r[1] / k, r[2] / k, r[3] / k)

        def _poll_windows(self):
            try:
                raw, fg = self.watcher.snapshot(int(self.winId()))
                for w in raw:
                    w['rect'] = self._logical(w['rect'])
                wins = filter_windows(raw, int(self.winId()))
                self._wins = wins
                fgr = None
                if fg:
                    fgr = self._logical(fg['rect'])
                    if fg['title'] != self._fg_title:
                        self._fg_title = fg['title']
                        self._mood = mood_from_title(fg['title'])
                self._fg = fgr
                full = False
                if fg and fg['cls'] not in SHELL_CLASSES and fg['title']:
                    for scr in QtGui.QGuiApplication.screens():
                        g = scr.geometry()
                        if (fgr[0] <= g.x() + 1 and fgr[1] <= g.y() + 1 and
                                fgr[2] >= g.x() + g.width() - 1 and fgr[3] >= g.y() + g.height() - 1):
                            full = True
                            break
                # Purely informational for the animation policy: whether the
                # pet hides or stays visible is still decided by hide_fs below.
                self.brain.fullscreen = full
                if self.hide_fs and full and not self.hidden:
                    self.hidden = True
                    self.hide()
                    self.timer.setInterval(500)
                elif not full and self.hidden:
                    self.hidden = False
                    self.show()
                    self._last = time.perf_counter()
                    self.timer.setInterval(16 if self.fps60 else 33)
            except Exception:
                pass

        def toggle_react(self):
            self.react = not self.react
            if not self.react:
                self._wins, self._mood, self._fg = [], None, None
                self.brain.fullscreen = False
                if self.brain.state in ('perch', 'perch_go'):
                    self.brain._begin('idle', 2.0)
            self._save()

        def toggle_fullscreen_visibility(self):
            self.show_fullscreen = not self.show_fullscreen
            self.hide_fs = (not self.show_fullscreen) and self.watcher.ok
            if self.show_fullscreen and self.hidden:
                self.hidden = False; self.show(); self._last = time.perf_counter()
                self.timer.setInterval(16 if self.fps60 else 33)
            self._save()

        # ---------------------------------------------------------- mouse
        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton:
                gp = e.globalPosition().toPoint()
                self._press = (gp.x(), gp.y()); self._dragging = False
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            elif e.button() == Qt.MouseButton.RightButton:
                self.show_menu(e.globalPosition().toPoint())

        def mouseMoveEvent(self, e):
            if self._press is None:
                return
            gp = e.globalPosition().toPoint()
            if not self._dragging and math.hypot(gp.x() - self._press[0], gp.y() - self._press[1]) > 5:
                self._dragging = True
                self.brain.begin_drag(self._press[0], self._press[1])

        def mouseReleaseEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton and self._press is not None:
                if self._dragging:
                    self.brain.end_drag()
                else:
                    self.brain.click()
                self._press = None; self._dragging = False
                self.setCursor(Qt.CursorShape.OpenHandCursor)

        def mouseDoubleClickEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton:
                self._press = None
                self.brain.double_click()

        # ---------------------------------------------------------- settings / menu
        def _save(self):
            self.settings.update(scale=self.S, mode=self.brain.mode, sleepy=self.brain.allow_sleep,
                                 fps60=self.fps60, sound=self.sound_on, sound_volume=self.sound_volume,
                                 cursor_curiosity=self.brain.cursor_curiosity,
                                 react=self.react, show_fullscreen=self.show_fullscreen,
                                 hide_fs=self.hide_fs, think=self.thinking,
                                 companion_bridge=self.bridge_enabled,
                                 desktop_icon_moves=self.icon_moves_enabled,
                                 desktop_icon_autonomy=self.icon_autonomy_enabled,
                                 desktop_icon_control=self.icon_control_mode,
                                 cursor_tug_game=self.cursor_tug_enabled,
                                 animation_pack_enabled=self.brain.anim_enabled,
                                 animation_travel_enabled=self.brain.anim_travel)
            save_settings(self.settings)

        def set_scale(self, s):
            cx, cy = self.brain.x, self.brain.y
            self.S = float(s)
            pad = float(self.brain.animation_pad())
            self._pad = pad
            self.W = int(round((DESIGN_W + 2.0 * pad) * self.S))
            self.H = int(round((DESIGN_H + 2.0 * pad) * self.S))
            self.setFixedSize(self.W, self.H)
            self.brain.set_size(self.W, self.H, self.S)
            self.brain.view_pad = pad
            self.brain.x, self.brain.y = cx, cy
            wx = int(math.floor(cx - self.W / 2.0))
            wy = int(math.floor(cy - self.H / 2.0))
            self.move(wx, wy)
            self._wpos = (wx, wy)
            self._save()

        def set_mode(self, m):
            self.brain.set_mode(m, user=True)
            if m != 'follow': self.brain.stop_cursor_behavior(disable=False)
            self._save()

        def toggle_sleepy(self):
            self.brain.allow_sleep = not self.brain.allow_sleep; self._save()

        def toggle_cursor_curiosity(self):
            if self.brain.cursor_curiosity:
                self.brain.stop_cursor_behavior()
            else:
                self.brain.cursor_curiosity = True
                self.brain.cursor_invite_in = random.uniform(4.0, 8.0)
            self._save()

        def toggle_cursor_tug(self):
            self.cursor_tug_enabled = not self.cursor_tug_enabled
            if not self.cursor_tug_enabled:
                self._cursor_tug = None
            else:
                self._next_cursor_tug_at = time.monotonic() + random.uniform(12.0, 25.0)
            self._save()

        def toggle_fps(self):
            self.fps60 = not self.fps60
            self._sync_fps(); self._save()

        def toggle_animation_pack(self):
            self.brain.anim_enabled = not self.brain.anim_enabled
            if not self.brain.anim_enabled and self.brain.anim is not None:
                # Hard reset so the compact window can shrink immediately and
                # re-enabling starts from a clean pose, not a stale mid-clip.
                a = self.brain.anim
                a.clip = None; a.springs.clear(); a.particles = []
                a.requests = []; a.out = {}
                self.brain.mv = None; self.brain.mv_stack = []
            self._save()

        def toggle_animation_travel(self):
            self.brain.anim_travel = not self.brain.anim_travel
            self._save()

        def toggle_sound(self):
            self.sound_on = not self.sound_on
            if self.sound_on and self.sound_volume == 0: self.sound_volume = 65
            self._save()

        def test_sound(self):
            self.sound_on = True
            if self.sound_volume == 0: self.sound_volume = 65
            self.sound_bank.next_ok['click'] = 0.0
            self._play_sound('click')
            self.bubble.show_text('Meow sound test.', self._anchor(), status=True)
            self._save()

        def set_sound_volume(self, amount):
            self.sound_volume = max(0, min(100, int(amount)))
            self.sound_on = self.sound_volume > 0
            self._save()

        def set_motion(self, reduced):
            self.brain.motion_scale = .48 if reduced else 1.0
            self.settings['reduced_motion'] = bool(reduced); self._save()

        def set_energy(self, amount):
            self.brain.energy_level = float(amount)
            self.settings['energy_level'] = float(amount); self._save()

        def set_weather(self, mood):
            self.brain.weather_mood = mood
            self.settings['weather_mood'] = mood; self._save()

        def edit_name(self):
            name, ok = QtWidgets.QInputDialog.getText(self, 'Chill Pet name', 'Name:', text=str(self.settings.get('pet_name','Chill Pet'))[:24])
            if ok and name.strip(): self.settings['pet_name'] = name.strip()[:24]; self._save()

        def export_profile(self):
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export pet profile', 'chill-pet-profile.json', 'JSON (*.json)')
            if not path: return
            try:
                data = dict(profile_version=1, settings=self.settings, personality=dict(disp=self.pet_state.disp,
                            comfort=self.pet_state.comfort, wary=self.pet_state.wary, places=self.pet_state.places,
                            achievements=self.pet_state.achievements))
                with open(path, 'w') as f: json.dump(data, f, indent=2)
                self.bubble.show_text('Profile exported.', self._anchor(), status=True)
            except Exception:
                self.bubble.show_text('Could not export profile.', self._anchor(), status=True)

        def import_profile(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Import pet profile', '', 'JSON (*.json)')
            if not path: return
            try:
                with open(path, 'r') as f: data = json.load(f)
                if not isinstance(data, dict) or not isinstance(data.get('settings'), dict): raise ValueError()
                safe = ('pet_name','sound','sound_volume','mode','sleepy','cursor_curiosity','energy_level','weather_mood','reduced_motion')
                self.settings.update(dict((k, data['settings'][k]) for k in safe if k in data['settings']))
                p = data.get('personality', {})
                if isinstance(p, dict):
                    self.pet_state.disp = norm_disp(p.get('disp', self.pet_state.disp)); self.pet_state.comfort = clamp(PetState._num(p.get('comfort'),.5),0,1)
                    self.pet_state.wary = clamp(PetState._num(p.get('wary'),0),0,1); self.pet_state.places = list(p.get('places') or [])[-4:]
                    self.pet_state.achievements = [str(x)[:32] for x in (p.get('achievements') or [])[-16:]]
                    self.pet_state._dirty=True; self.pet_state.save(force=True)
                self.bubble.show_text('Profile imported — restart for every option.', self._anchor(), status=True); self._save()
            except Exception:
                self.bubble.show_text('That profile could not be read.', self._anchor(), status=True)

        def toggle_companion_bridge(self):
            if self.bridge_enabled:
                self.bridge_enabled = False
                self.bridge.stop()
            else:
                self.bridge_enabled = self.bridge.start()
                if not self.bridge_enabled:
                    self.bubble.show_text('Companion port 8765 is busy.', self._anchor(), status=True)
            self._save()

        def like_youtube(self):
            if not self.bridge_enabled:
                self.bubble.show_text('Enable the companion bridge first.', self._anchor(), status=True); return
            self.bridge.request_like()
            self.bubble.show_text('Sent a visible Like request to YouTube.', self._anchor(), status=True)

        def capture_foreground(self):
            if not self._fg:
                self.bubble.show_text('No foreground window to capture.', self._anchor(), status=True); return
            try:
                screen = QtGui.QGuiApplication.screenAt(QtCore.QPoint(int(self._fg[0]), int(self._fg[1]))) or QtGui.QGuiApplication.primaryScreen()
                g = screen.geometry(); ratio = screen.devicePixelRatio()
                pix = screen.grabWindow(0, int((self._fg[0]-g.x())*ratio), int((self._fg[1]-g.y())*ratio),
                                       int((self._fg[2]-self._fg[0])*ratio), int((self._fg[3]-self._fg[1])*ratio))
                folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'captures'); os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, 'foreground-%s.png' % datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
                if not pix.save(path): raise RuntimeError()
                self.activity_log.append(dict(t=datetime.datetime.now().strftime('%H:%M:%S'),source='local',event='foreground capture'))
                self.bubble.show_text('Saved local foreground capture.', self._anchor(), status=True)
            except Exception:
                self.bubble.show_text('Capture was unavailable.', self._anchor(), status=True)

        def choose_visual_folder(self):
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Choose a folder for visual awareness only')
            if not folder: return
            try:
                names = [x for x in os.listdir(folder) if not x.startswith('.')][:80]
                # Only names/count are retained for this session; this is used as
                # a symbolic local "place", never as permission to modify files.
                self.settings['visual_folder'] = folder
                self.brain.rx.state.note_place('chosen folder', 'desktop')
                self.activity_log.append(dict(t=datetime.datetime.now().strftime('%H:%M:%S'),source='local',event='folder viewed (%d items)' % len(names)))
                self.bubble.show_text('I can perch around this folder — %d items.' % len(names), self._anchor(), status=True)
                self._save()
            except Exception:
                self.bubble.show_text('That folder could not be read.', self._anchor(), status=True)

        def toggle_icon_moves(self):
            if not self.icon_mover.ok or self.icon_mover.count() <= 0:
                self.bubble.show_text('Windows desktop icon view is unavailable.', self._anchor(), status=True); return
            self.icon_moves_enabled = not self.icon_moves_enabled
            if not self.icon_moves_enabled:
                self.icon_control_mode = 'manual'; self.icon_autonomy_enabled = False; self.brain.allow_icon_autonomy = False
            self.settings['desktop_icon_moves'] = self.icon_moves_enabled; self._save()

        def set_icon_control(self, mode):
            if not self.icon_moves_enabled:
                self.bubble.show_text('Enable desktop icon moves first.', self._anchor(), status=True); return
            self.icon_control_mode = mode
            self.icon_autonomy_enabled = mode == 'auto'
            self.brain.allow_icon_autonomy = mode != 'manual'
            self._icon_request_until = 0.0
            self.settings['desktop_icon_control'] = mode; self.settings['desktop_icon_autonomy'] = self.icon_autonomy_enabled; self._save()

        def approve_icon_request(self):
            if time.monotonic() > self._icon_request_until:
                self.bubble.show_text('No current carry request.', self._anchor(), status=True); return
            self._icon_request_until = 0.0
            self._start_icon_carry(autonomous=False)

        def _start_icon_carry(self, autonomous=False):
            if self._icon_job is not None: return False
            job, msg = self.icon_mover.plan_random()
            if not job:
                if not autonomous: self.bubble.show_text(msg, self._anchor(), status=True)
                return False
            if self.brain.anim is not None:
                self.brain.anim.stop(0.12)
            self.brain.mv = None; self.brain.mv_stack = []
            self.brain._begin('icon_carry', 6.0)
            job.update(started=time.perf_counter(), duration=4.80, grabbed=False, released=False,
                       pet_start=(self.brain.x, self.brain.y), offset=(42*self.S, -38*self.S), autonomous=autonomous)
            self._icon_job = job
            self.timer.setInterval(16)  # steady real-time icon and pet interpolation
            self._next_auto_icon_at = time.monotonic() + random.uniform(95.0, 180.0)
            if not autonomous: self.bubble.show_text('Picking this one up…', self._anchor(), status=True)
            return True

        def move_desktop_icon(self):
            if not self.icon_moves_enabled:
                self.bubble.show_text('Enable desktop icon moves first.', self._anchor(), status=True); return
            self._start_icon_carry(autonomous=False)

        def view_log(self):
            text = '\n'.join('%s  %s: %s' % (x['t'],x['source'],x['event']) for x in self.activity_log[-30:]) or 'No local activity yet.'
            QtWidgets.QMessageBox.information(self, 'Chill Pet local activity', text)

        def clear_log(self):
            self.activity_log = []
            self.bubble.show_text('Local activity log cleared.', self._anchor(), status=True)

        def quit_app(self):
            self._save()
            self.pet_state.save(force=True)
            self.thought_engine.stop(wait=0.2)
            self.bridge.stop()
            QtWidgets.QApplication.quit()

        def _fill_menu(self, m):
            def add(menu, text, fn, checked=None):
                a = QtGui.QAction(text, menu)
                if checked is not None:
                    a.setCheckable(True); a.setChecked(bool(checked))
                a.triggered.connect(lambda *_: fn())
                menu.addAction(a)
                return a
            b = self.brain
            add(m, 'Say hi!', b.cheer)
            if b.is_sleeping():
                add(m, 'Wake up', b.wake_up)
            else:
                add(m, 'Take a nap', b.go_sleep)
            m.addSeparator()
            sub = m.addMenu('Behavior')
            add(sub, 'Chill (wander around)', lambda: self.set_mode('chill'), b.mode == 'chill')
            add(sub, 'Follow my cursor', lambda: self.set_mode('follow'), b.mode == 'follow')
            add(sub, 'Stay right here', lambda: self.set_mode('still'), b.mode == 'still')
            sub.addSeparator()
            add(sub, 'Allow napping', self.toggle_sleepy, b.allow_sleep)
            add(sub, 'Curious about my cursor', self.toggle_cursor_curiosity, b.cursor_curiosity)
            add(sub, 'Cursor tug game (Escape cancels)', self.toggle_cursor_tug, self.cursor_tug_enabled)
            sub = m.addMenu('Size')
            for name, val in SIZES:
                add(sub, name, (lambda v=val: self.set_scale(v)), abs(self.S - val) < 0.02)
            if pet_anims is not None and b.anim is not None:
                sub = m.addMenu('Animations')
                add(sub, 'Animation pack', self.toggle_animation_pack, b.anim_enabled)
                add(sub, 'Travel clips (moves the window)', self.toggle_animation_travel, b.anim_travel)
                sub.addSeparator()
                for n in pet_anims.CLIPS:
                    add(sub, n.replace('_', ' ').title(),
                        (lambda nm=n: self.play_animation(nm)))
            add(m, 'Smooth animation (60 fps)', self.toggle_fps, self.fps60)
            add(m, 'Soft reaction sounds', self.toggle_sound, self.sound_on)
            add(m, 'Test sound now', self.test_sound)
            vol = m.addMenu('Sound volume')
            for amount in (0, 20, 40, 65, 85, 100):
                add(vol, ('Mute' if amount == 0 else '%d%%' % amount),
                    (lambda v=amount: self.set_sound_volume(v)), self.sound_volume == amount)
            add(m, 'Let him think', lambda: self.set_thinking(not self.thinking), self.thinking)
            add(m, 'Enable Brave / VS Code companion bridge', self.toggle_companion_bridge, self.bridge_enabled)
            local = m.addMenu('Advanced local actions')
            add(local, 'Like current YouTube video', self.like_youtube)
            add(local, 'Capture foreground app locally', self.capture_foreground)
            add(local, 'Choose folder for visual awareness…', self.choose_visual_folder)
            add(local, 'Let him rearrange desktop icons', self.toggle_icon_moves, self.icon_moves_enabled)
            mode = local.addMenu('Icon carry control')
            add(mode, 'Manual only', lambda: self.set_icon_control('manual'), self.icon_control_mode == 'manual')
            add(mode, 'Assisted: ask me first', lambda: self.set_icon_control('assisted'), self.icon_control_mode == 'assisted')
            add(mode, 'Autonomous with cooldown', lambda: self.set_icon_control('auto'), self.icon_control_mode == 'auto')
            add(local, 'Approve pet carry request', self.approve_icon_request)
            add(local, 'Move one desktop icon now', self.move_desktop_icon)
            add(local, 'View local activity log', self.view_log)
            add(local, 'Clear local activity log', self.clear_log)
            custom = m.addMenu('Personality & accessibility')
            add(custom, 'Set pet name…', self.edit_name)
            motion = bool(self.settings.get('reduced_motion', False))
            add(custom, 'Reduced motion', lambda: self.set_motion(not motion), motion)
            energy = custom.addMenu('Energy level')
            for label, value in (('Low', .35), ('Calm', .55), ('Playful', .75), ('Zoomy', 1.0)):
                add(energy, label, lambda v=value: self.set_energy(v), abs(self.brain.energy_level-value) < .05)
            weather = custom.addMenu('Weather ambience (manual)')
            for label, value in (('Off', 'off'), ('Clear', 'clear'), ('Rainy day', 'rain')):
                add(weather, label, lambda v=value: self.set_weather(v), self.brain.weather_mood == value)
            custom.addSeparator()
            add(custom, 'Export personality profile…', self.export_profile)
            add(custom, 'Import personality profile…', self.import_profile)
            if self.watcher.ok:
                add(m, 'Sit on & react to my windows', self.toggle_react, self.react)
                add(m, 'Stay visible over fullscreen apps', self.toggle_fullscreen_visibility, self.show_fullscreen)
            else:
                a = add(m, 'Window reactions (Windows only)', lambda: None)
                a.setEnabled(False)
            m.addSeparator()
            add(m, 'Quit', self.quit_app)

        def show_menu(self, gpos):
            m = QtWidgets.QMenu()
            self._fill_menu(m)
            m.exec(gpos)

        # ---------------------------------------------------------- tray
        def _make_tray(self):
            self.tray = None
            try:
                if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
                    return
                pm = QtGui.QPixmap(64, 64)
                pm.fill(Qt.GlobalColor.transparent)
                p = QtGui.QPainter(pm)
                p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
                P = default_pose(); P['halo'] = 0.0; P['oy'] = 16.0
                draw_pet(QtCanvas(p), P, 64, 64, 0.17)
                p.end()
                self.tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(pm), self)
                self.tray_menu = QtWidgets.QMenu()
                self._fill_menu(self.tray_menu)
                self.tray_menu.aboutToShow.connect(self._refresh_tray_menu)
                self.tray.setContextMenu(self.tray_menu)
                self.tray.setToolTip('Chill Pet')
                self.tray.show()
            except Exception:
                self.tray = None

        def _refresh_tray_menu(self):
            self.tray_menu.clear()
            self._fill_menu(self.tray_menu)

    return PetWindow


def main(argv=None):
    ap = argparse.ArgumentParser(description='A chill desktop pet.')
    ap.add_argument('--scale', type=float, help='size multiplier (0.35 - 1.4, default 0.5)')
    ap.add_argument('--mode', choices=['chill', 'follow', 'still'])
    ap.add_argument('--no-sleep', action='store_true', help='never take naps')
    ap.add_argument('--think', action='store_true', help='enable the optional local reaction model')
    ap.add_argument('--no-think', action='store_true', help='disable the optional local reaction model')
    ap.add_argument('--llm-debug', action='store_true', help='print local model timing diagnostics')
    ap.add_argument('--model', help='path to a compatible local GGUF model')
    args = ap.parse_args(argv)

    qt = import_qt(True)
    if qt is None:
        print('Could not load Qt. Install it with:  pip install PySide6')
        try:
            input('Press Enter to close...')
        except Exception:
            pass
        return 1
    QtCore, QtGui, QtWidgets = qt
    settings = load_settings()
    if args.scale: settings['scale'] = max(0.2, min(2.0, args.scale))
    if args.mode: settings['mode'] = args.mode
    if args.no_sleep: settings['sleepy'] = False
    if args.think: settings['think'] = True
    if args.no_think: settings['think'] = False

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    PetWindow = build(QtCore, QtGui, QtWidgets)
    win = PetWindow(settings, args)
    win.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
