# -*- coding: utf-8 -*-
"""
pet_anims.py - a drop-in animation layer for Chill Pet.

* Pure Python, no Qt, no imports from the pet.  It only reads/writes a plain `pose` dict.
* Every clip is ADDITIVE: it returns offsets that are layered on top of whatever the
  brain already does (breathing, gaze, wandering...), so nothing ever "takes over".
* Smoothness comes from: smootherstep/hermite easing (zero-velocity or C1 keys),
  fade-in / fade-out envelopes, optional follow-through springs, and authored overshoot.

Channels a clip may return (all optional)
    lift  dx            design px (lift = up).  Converted with scale S in apply().
    rot                 degrees added to whole-body rotation
    sx sy               MULTIPLIERS (1 = neutral; negative sx mirrors = fake 3-D flip)
    head_rot head_dy body_rot breathe ear_l ear_r (+ = up)
    tail_rot tail_bend tail_amp tail_phase
    look_x look_y tongue tongue_sway blink (max-blended)
    shadow              multiplier on the ground shadow
    wave wave_k wave_ph jelly warp (amplitude px / spatial freq / phase)

Host requests (read player.requests after update, then clear)
    ('push', dx, dy, dur, ease)   ('pop', dur, ease)   ('teleport',)   ('sound', name)
"""
import math, random

# ----------------------------------------------------------------------------- math
def clamp(v, a, b):
    return a if v < a else (b if v > b else v)

def seg(t, a, b):
    return clamp((t - a) / (b - a), 0.0, 1.0) if b > a else (1.0 if t >= a else 0.0)

def sm(u):                                   # smootherstep: zero velocity AND acceleration at ends
    u = clamp(u, 0.0, 1.0)
    return u * u * u * (u * (u * 6 - 15) + 10)

def osc(t, hz, ph=0.0):
    return math.sin(2 * math.pi * hz * t + ph)

def bump(t, a, b):                           # 0 -> 1 -> 0 between a and b, perfectly smooth
    return math.sin(math.pi * seg(t, a, b)) ** 2

def decay(t, rate):
    return math.exp(-rate * max(0.0, t))

def integ(f, t, step=0.01):                  # numeric integral of f over [0,t] (for chirps / spin-ups)
    n = int(t / step); acc = 0.0
    for i in range(n):
        acc += f((i + 0.5) * step) * step
    return acc + f(t) * (t - n * step) * 0.5

# ----------------------------------------------------------------------------- easings
def e_out_cubic(u): return 1 - (1 - u) ** 3
def e_in_cubic(u): return u ** 3
def e_out_sine(u): return math.sin(u * math.pi / 2)
def e_in_sine(u): return 1 - math.cos(u * math.pi / 2)
def e_out_back(u, s=1.8): return 1 + (s + 1) * (u - 1) ** 3 + s * (u - 1) ** 2
def e_in_back(u, s=1.8): return (s + 1) * u ** 3 - s * u ** 2
def e_out_elastic(u):
    if u <= 0: return 0.0
    if u >= 1: return 1.0
    return 2 ** (-10 * u) * math.sin((u * 10 - 0.75) * (2 * math.pi) / 3) + 1
def e_out_bounce(u):
    n, d = 7.5625, 2.75
    if u < 1 / d: return n * u * u
    if u < 2 / d: u -= 1.5 / d; return n * u * u + 0.75
    if u < 2.5 / d: u -= 2.25 / d; return n * u * u + 0.9375
    u -= 2.625 / d; return n * u * u + 0.984375

EASE = {'smooth': sm, 'lin': lambda u: u, 'out_cubic': e_out_cubic, 'in_cubic': e_in_cubic,
        'out_sine': e_out_sine, 'in_sine': e_in_sine, 'out_back': e_out_back, 'in_back': e_in_back,
        'out_elastic': e_out_elastic, 'out_bounce': e_out_bounce}

def kf(t, keys):
    """keyframes [(time, value, ease_into_this_key), ...]; eases are applied per segment."""
    if t <= keys[0][0]:
        return keys[0][1]
    for i in range(1, len(keys)):
        k = keys[i]
        if t <= k[0]:
            t0, v0 = keys[i - 1][0], keys[i - 1][1]
            u = (t - t0) / (k[0] - t0) if k[0] > t0 else 1.0
            return v0 + (k[1] - v0) * EASE[k[2] if len(k) > 2 else 'smooth'](u)
    return keys[-1][1]

def hermite(t, keys):
    """C1-continuous spline through (time, value) keys - flowing motion with no stop-go at keys."""
    n = len(keys)
    if t <= keys[0][0]: return keys[0][1]
    if t >= keys[-1][0]: return keys[-1][1]
    for i in range(n - 1):
        t0, v0 = keys[i][0], keys[i][1]
        t1, v1 = keys[i + 1][0], keys[i + 1][1]
        if t <= t1:
            def tan(j):
                a, b = keys[max(0, j - 1)], keys[min(n - 1, j + 1)]
                return (b[1] - a[1]) / (b[0] - a[0])
            h = t1 - t0; u = (t - t0) / h
            u2, u3 = u * u, u * u * u
            return ((2 * u3 - 3 * u2 + 1) * v0 + (u3 - 2 * u2 + u) * h * tan(i)
                    + (-2 * u3 + 3 * u2) * v1 + (u3 - u2) * h * tan(i + 1))

# ----------------------------------------------------------------------------- spring (follow-through)
class Spring(object):
    __slots__ = ('x', 'v', 'k', 'c')
    def __init__(self, k=120.0, c=12.0):
        self.x = 0.0; self.v = 0.0; self.k = k; self.c = c
    def step(self, target, dt):
        n = max(1, int(dt / 0.006) + 1); h = dt / n
        for _ in range(n):
            self.v += (self.k * (target - self.x) - self.c * self.v) * h
            self.x += self.v * h
        return self.x

MUL = ('sx', 'sy', 'shadow')                 # neutral = 1
SET = ('wave_k', 'wave_ph')                  # absolute, not enveloped
ADD = ('rot', 'head_rot', 'head_dy', 'body_rot', 'breathe', 'ear_l', 'ear_r', 'tail_rot', 'tail_bend',
       'tail_amp', 'tail_phase', 'look_x', 'look_y', 'tongue', 'tongue_sway', 'wave')

# ----------------------------------------------------------------------------- clip
class Clip(object):
    def __init__(self, name, dur, fn, tags=('idle',), events=(), fade=(0.08, 0.18), lag=None,
                 weight=1.0, cooldown=25.0, travel=False, desc=''):
        self.name, self.dur, self.fn, self.tags = name, dur, fn, tuple(tags)
        self.events = sorted(events, key=lambda e: e[0])
        self.fade, self.lag, self.weight, self.cooldown = fade, lag or {}, weight, cooldown
        self.travel, self.desc = travel, desc

def fx(t, kind, x, y, vx=0.0, vy=-20.0, life=1.4, size=9.0, rot=0.0, hold=0.5, grow=1.6):
    return (t, 'fx', dict(kind=kind, x=x, y=y, vx=vx, vy=vy, life=life, size=size, rot=rot, hold=hold, grow=grow))

CLIPS = {}
def clip(name, dur, **kw):
    def deco(fn):
        CLIPS[name] = Clip(name, dur, fn, **kw)
        return fn
    return deco

# =============================================================================== THE CLIPS
# 1 ---------------------------------------------------------------- jellyfish drift
@clip('jelly_drift', 3.8, tags=('idle', 'chill'), lag={'ear_l': (55, 7.5), 'ear_r': (55, 7.5)},
      desc='Floats like a jellyfish: bell-pulse thrust, glide, trailing ears and tail.')
def _jelly_drift(t, o):
    T = 1.2
    ph = (t / T) % 1.0
    thrust = (math.sin(math.pi * min(ph / 0.36, 1.0)) ** 1.6) if ph < 0.36 else 0.0
    return dict(lift=9 * osc(t, 1 / T, -0.9) + 3 * sm(seg(t, 0, 1)),
                sy=1 - 0.08 * thrust, sx=1 + 0.06 * thrust,
                ear_l=20 * osc(t, 1 / T, -1.5), ear_r=20 * osc(t, 1 / T, -1.1),
                tail_amp=0.35, tail_phase=1.4 * osc(t, 1 / T, -1.2),
                wave=2.6, wave_k=0.048, wave_ph=-2 * math.pi * t / T,
                head_rot=3 * osc(t, 0.5 / T), blink=0.35, rot=1.5 * osc(t, 0.5 / T, 1))

# 2 ---------------------------------------------------------------- inflate & pop
@clip('inflate_pop', 3.1, tags=('idle', 'playful'),
      events=[(0.25, 'sound', 'inhale'), (1.97, 'sound', 'pop'),
              fx(1.97, 'ring', 0, -30, vy=0, life=0.55, size=26),
              fx(2.0, 's', -60, -70, -90, 30, 0.8, 8), fx(2.0, 's', 60, -60, 90, 30, 0.8, 7)],
      desc='Puffs up like a balloon, trembles, pops back with an elastic wobble.')
def _inflate(t, o):
    s = kf(t, [(0, 1.0), (0.25, 0.95), (1.05, 1.26, 'out_cubic'), (1.9, 1.30), (2.02, 0.86, 'in_cubic'),
               (2.75, 1.0, 'out_elastic'), (3.1, 1.0)])
    return dict(sx=s, sy=s * 1.02,
                rot=1.7 * osc(t, 14) * bump(t, 1.15, 1.95),
                lift=kf(t, [(0, 0), (1.05, 12, 'out_cubic'), (1.95, 13), (2.08, 24, 'out_cubic'),
                            (2.55, 0, 'in_cubic'), (3.1, 0)]),
                blink=0.6 * bump(t, 1.0, 2.0),
                ear_l=12 * sm(seg(t, .25, 1.05)) - 40 * bump(t, 1.95, 2.45),
                ear_r=12 * sm(seg(t, .25, 1.05)) - 40 * bump(t, 1.95, 2.45),
                tongue=0.35 * bump(t, 1.9, 2.5), tail_amp=0.6 * bump(t, 1.95, 2.8))

# 3 ---------------------------------------------------------------- metronome sway
@clip('metronome', 4.6, tags=('idle', 'chill', 'calm'), lag={'tail_rot': (70, 8)},
      events=[(0.9 * k / 1.0 * 0.5 + 0.55, 'sound', 'tick') for k in range(7)],
      desc='A hypnotic pendulum swing pivoting at the tail; eyes and tongue follow the beat.')
def _metronome(t, o):
    f = 0.9
    A = 1.0 * sm(seg(t, 0, 0.7)) * (1 - sm(seg(t, 3.7, 4.6)))
    th = 9.5 * A * osc(t, f)
    R = 125.0
    return dict(rot=th, dx=R * math.sin(math.radians(th)), lift=-R * (1 - math.cos(math.radians(th))),
                look_x=0.9 * A * osc(t, f, 0.5), ear_l=-9 * A * osc(t, f, 0.4), ear_r=-9 * A * osc(t, f, 0.4),
                tail_rot=-15 * A * osc(t, f, 0.8), head_rot=4 * A * osc(t, f, 0.6),
                blink=0.25 * A, tongue=0.25 * A, tongue_sway=0.7 * A * osc(t, f, 0.4))

# 4 ---------------------------------------------------------------- rubber-band boing
@clip('boing', 2.4, tags=('idle', 'playful'),
      events=[(0.62, 'sound', 'boing'), fx(0.62, 's', 0, -150, 0, -30, 0.6, 8)],
      desc='Squashes down like a spring, stretches sky-high, then boings itself still.')
def _boing(t, o):
    sy = kf(t, [(0, 1), (0.35, 0.80), (0.62, 1.32, 'out_cubic'), (0.92, 0.84, 'in_cubic'),
                (1.22, 1.12, 'out_cubic'), (1.55, 0.96), (1.95, 1.0), (2.4, 1.0)])
    e = kf(t, [(0, 0), (0.35, -25), (0.62, 45, 'out_back'), (0.92, -20), (1.35, 10), (1.95, 0), (2.4, 0)])
    return dict(sy=sy, sx=1 / math.sqrt(sy),
                lift=kf(t, [(0, 0), (0.35, -4), (0.62, 20, 'out_cubic'), (0.92, -3, 'in_cubic'), (1.25, 5), (1.7, 0)]),
                ear_l=e, ear_r=e, tail_amp=0.8 * bump(t, 0.45, 1.5), tongue=0.4 * bump(t, 0.6, 1.3),
                blink=0.55 * (bump(t, 0.15, 0.5) + bump(t, 0.85, 1.1)))

# 5 ---------------------------------------------------------------- ear-copter
@clip('ear_copter', 3.6, tags=('idle', 'playful'),
      events=[(0.3, 'sound', 'whirr'), (3.3, 'sound', 'land')],
      desc='Spins both ears like rotor blades, lifts off, hovers, and touches down with a squash.')
def _copter(t, o):
    spd = lambda x: 7.5 * sm(seg(x, 0.3, 1.3)) * (1 - sm(seg(x, 2.5, 3.3)))
    ph = 2 * math.pi * integ(spd, t)
    up = sm(seg(t, 0.3, 1.1)) * (1 - sm(seg(t, 2.7, 3.4)))
    e = 26 * up + 36 * math.sin(ph) * min(1.0, spd(t) / 2.0)
    return dict(ear_l=e, ear_r=e, lift=30 * sm(seg(t, 0.45, 1.5)) * (1 - sm(seg(t, 2.6, 3.3))) + 2.2 * osc(t, 2.6) * up,
                rot=3 * osc(t, 0.8) * up, tail_amp=0.6 * up, tongue=0.25 * up, look_y=-0.3 * up, blink=0.4 * up,
                sy=1 - 0.11 * bump(t, 3.15, 3.5), sx=1 + 0.08 * bump(t, 3.15, 3.5), shadow=1 - 0.5 * up)

# 6 ---------------------------------------------------------------- jelly wobble
@clip('jelly_wobble', 2.7, tags=('react', 'playful'), fade=(0.03, 0.2),
      events=[(0.0, 'sound', 'boing')],
      desc='Gelatin body-wave after a bump: a true non-rigid ripple that decays naturally.')
def _jelly_wobble(t, o):
    e = decay(t, 2.2)
    sy = 1 + 0.10 * e * math.cos(2 * math.pi * 3.0 * t)
    return dict(wave=9 * e, wave_k=0.046, wave_ph=-2 * math.pi * 2.3 * t,
                sy=sy, sx=1 / math.sqrt(sy), dx=14 * e * osc(t, 1.5), rot=6 * e * osc(t, 1.5, 0.3),
                ear_l=32 * e * osc(t, 2.5, 0.4), ear_r=32 * e * osc(t, 2.5, 0.9),
                tail_bend=0.7 * e * osc(t, 1.7), tongue=0.35 * e, blink=0.5 * bump(t, 0, 0.35))

# 7 ---------------------------------------------------------------- coin flip
@clip('coin_flip', 2.0, tags=('idle', 'playful', 'show'),
      events=[(0.42, 'sound', 'whoosh'), (1.52, 'sound', 'land'), fx(1.52, 's', 0, -140, 0, -25, 0.7, 8)],
      desc='Hops and spins around its vertical axis like a coin (real mirrored flip).')
def _coin(t, o):
    flips = o.get('flips', 1)
    th = 2 * math.pi * flips * sm(seg(t, 0.38, 1.38))
    c = math.cos(th)
    u = seg(t, 0.32, 1.5)
    sy = kf(t, [(0, 1), (0.32, 0.86), (0.45, 1.12, 'out_cubic'), (1.4, 1.02), (1.52, 0.86, 'out_cubic'),
                (1.9, 1.0, 'out_elastic')])
    return dict(sx=(c if abs(c) > 0.035 else (0.035 if c >= 0 else -0.035)) * (1 if 0.32 < t < 1.5 else 1.0) * (1 + 0.0),
                sy=sy, lift=44 * 4 * u * (1 - u), rot=5 * math.sin(th),
                ear_l=-18 * bump(t, 0.4, 1.45), ear_r=-18 * bump(t, 0.4, 1.45),
                tail_bend=0.9 * math.sin(th), blink=0.4 * bump(t, 0.3, 0.6), tongue=0.3 * bump(t, 0.5, 1.4))

# 8 ---------------------------------------------------------------- heartbeat
def _hearts():
    return [fx(0.15 + 0.9 * k, 'heart', random.uniform(-26, 26), -150, random.uniform(-6, 6), -26, 1.7,
               random.uniform(7.5, 11)) for k in range(3)]

@clip('heartbeat', 3.3, tags=('affection', 'calm'), events=_hearts(),
      desc='A double-thump heartbeat pulse with hearts floating up. Great for nuzzle/blush moments.')
def _heartbeat(t, o):
    ph = (t / 0.9) % 1.0
    p = bump(ph, 0.0, 0.17) + 0.6 * bump(ph, 0.27, 0.42)
    a = sm(seg(t, 0, 0.5)) * (1 - sm(seg(t, 2.8, 3.3)))
    return dict(sx=1 + 0.075 * p * a, sy=1 + 0.075 * p * a, ear_l=(6 + 10 * p) * a, ear_r=(6 + 10 * p) * a,
                blink=0.45 * a, tail_amp=0.3 * a, tongue=0.1 * a, rot=1.5 * a * osc(t, 0.55),
                head_dy=-1.5 * p * a)

# 9 ---------------------------------------------------------------- rocket
@clip('rocket', 4.0, tags=('idle', 'show', 'travel'), travel=True, cooldown=40,
      events=[(0.55, 'push', dict(dx=0, dy=-230, dur=0.6, ease='in_cubic')), (0.55, 'sound', 'whoosh'),
              fx(0.55, 'ring', 0, 120, 0, 0, 0.5, 24)] +
             [fx(0.62 + 0.07 * k, 'ring', random.uniform(-10, 10), 125, 0, 280, 0.55, 12 - k * 0.5) for k in range(7)] +
             [(1.55, 'pop', dict(dur=1.85, ease='out_sine')), (3.4, 'sound', 'land')],
      desc='Crouch, blast straight up, hang, then parachute down on open ears. (needs host move support)')
def _rocket(t, o):
    sy = kf(t, [(0, 1), (0.5, 0.78), (0.6, 1.30, 'out_cubic'), (1.15, 1.20), (1.55, 1.0), (3.35, 1.0),
                (3.45, 0.82, 'out_cubic'), (3.95, 1.0, 'out_elastic')])
    ear = kf(t, [(0, 0), (0.5, -36), (0.6, -55, 'out_cubic'), (1.2, -50), (1.7, 58, 'out_back'), (3.3, 52), (3.5, 5, 'out_cubic'), (4.0, 0)])
    glide = bump(t, 1.6, 3.4)
    return dict(sy=sy, sx=1 / math.sqrt(sy), ear_l=ear + 6 * osc(t, 1.1) * glide, ear_r=ear - 6 * osc(t, 1.1) * glide,
                rot=7 * osc(t, 0.7) * glide, tail_amp=1.0 * bump(t, 0.5, 1.5), tail_bend=0.9 * bump(t, 0.6, 1.2),
                blink=0.6 * bump(t, 0.5, 1.2), look_y=-0.8 * bump(t, 1.1, 1.7), tongue=0.4 * bump(t, 0.6, 1.2),
                dx=6 * osc(t, 0.7) * glide, shadow=1.0)

# 10 --------------------------------------------------------------- ear wave hello
@clip('ear_wave', 2.7, tags=('idle', 'affection', 'greet'),
      events=[fx(0.5, 's', 105, -105, 20, -20, 0.8, 8)],
      desc='Raises one ear and waves hello, tilting the head; pick the side with opts side="L"/"R".')
def _ear_wave(t, o):
    side = o.get('side', 'R')
    env_in = sm(seg(t, 0, 0.4)); env_out = 1 - sm(seg(t, 2.1, 2.65))
    main = (72 * env_in + 22 * osc(t, 3.0) * sm(seg(t, 0.35, 0.75))) * env_out
    other = 6 * env_in * env_out
    sgn = 1 if side == 'R' else -1
    return dict(ear_r=main if side == 'R' else other, ear_l=main if side == 'L' else other,
                head_rot=7 * sgn * env_in * env_out, rot=2.5 * sgn * env_in * env_out,
                look_x=0.0, look_y=0.25 * env_in, blink=0.6 * bump(t, 1.2, 1.5), tongue=0.3 * bump(t, 0.4, 2.2),
                tail_amp=0.5 * env_in * env_out, lift=3.5 * abs(osc(t, 1.5)) * env_in * env_out)

# 11 --------------------------------------------------------------- hiccups
@clip('hiccup', 3.8, tags=('idle', 'react', 'playful'),
      events=[(0.55, 'sound', 'hic'), (1.45, 'sound', 'hic'), (2.0, 'sound', 'hic'), (2.9, 'sound', 'hic'),
              fx(0.58, 's', 70, -130, 30, -30, 0.6, 7), fx(1.48, 's', -70, -125, -30, -30, 0.6, 7),
              fx(2.03, 's', 60, -120, 30, -30, 0.6, 6), fx(2.93, 's', -60, -120, -30, -30, 0.6, 5)],
      desc='Four irregular hiccups, each a sharp jolt with stretch, ear-pop and tongue-blep.')
def _hiccup(t, o):
    out = dict(lift=0.0, sy=1.0, sx=1.0, ear_l=0.0, ear_r=0.0, tongue=0.0, blink=0.0, rot=0.0, head_dy=0.0)
    for ti, s in ((0.55, 1.0), (1.45, 0.9), (2.0, 0.7), (2.9, 0.5)):
        tau = t - ti
        if tau < 0:
            continue
        j = s * math.exp(-7 * tau) * math.sin(2 * math.pi * 3.2 * tau)
        pop = s * math.exp(-10 * tau)
        out['lift'] += 17 * j; out['sy'] += 0.10 * j; out['sx'] -= 0.06 * j
        out['ear_l'] += 38 * j; out['ear_r'] += 38 * j; out['rot'] += 2.5 * j
        out['tongue'] += 0.55 * pop; out['blink'] += 0.6 * math.exp(-12 * tau) * s; out['head_dy'] -= 2.5 * j
    out['blink'] = min(out['blink'], 0.9)
    return out

# 12 --------------------------------------------------------------- melt into a loaf
@clip('melt_loaf', 4.8, tags=('calm', 'sleepy'), fade=(0.1, 0.1),
      events=[fx(2.5, 'z', 58, -150, 12, -18, 2.4, 8), fx(3.3, 'z', 58, -150, 12, -18, 2.4, 7)],
      desc='Slowly melts into a contented loaf (eyes close, ears slump), then re-inflates like dough.')
def _melt(t, o):
    m = sm(seg(t, 0.2, 1.7)) * (1 - sm(seg(t, 3.7, 4.55)))
    reinflate = 0.07 * bump(t, 4.0, 4.7)
    return dict(sy=1 - 0.27 * m + reinflate, sx=1 + 0.2 * m - reinflate * 0.6, lift=-7 * m,
                ear_l=-42 * m, ear_r=-42 * m, tail_amp=-0.45 * m, head_dy=6 * m, head_rot=4 * m * osc(t, 0.25),
                blink=1.0 * sm(seg(t, 1.2, 2.1)) * (1 - sm(seg(t, 3.9, 4.2))), tongue=0.25 * m,
                breathe=0.03 * osc(t, 0.5) * m)

# 13 --------------------------------------------------------------- teleport
@clip('teleport', 2.4, tags=('idle', 'show', 'travel'), travel=True, cooldown=40,
      events=[(0.0, 'sound', 'charge'), (0.7, 'teleport', {}), (0.7, 'sound', 'pop'),
              fx(0.6, 'ring', 0, 0, 0, 0, 0.5, 30), fx(1.0, 'ring', 0, 0, 0, 0, 0.55, 34),
              fx(1.0, 's', -80, -60, -70, 20, 0.7, 8), fx(1.0, 's', 80, -50, 70, 20, 0.7, 8)],
      desc='Squeezes to a thin line, vanishes, pops back somewhere else with an elastic landing.')
def _teleport(t, o):
    sy = kf(t, [(0, 1), (0.12, 1.05), (0.45, 0.9), (0.62, 0.02, 'in_cubic'), (0.8, 0.02), (1.0, 1.30, 'out_cubic'),
                (1.5, 0.92), (1.85, 1.0, 'out_elastic'), (2.4, 1.0)])
    sx = kf(t, [(0, 1), (0.45, 1.12), (0.62, 1.7, 'in_cubic'), (0.8, 1.7), (1.0, 0.78, 'out_cubic'),
                (1.5, 1.08), (1.85, 1.0, 'out_elastic'), (2.4, 1.0)])
    return dict(sx=sx, sy=sy, shadow=kf(t, [(0, 1), (0.6, 0), (0.9, 0), (1.15, 1)]),
                rot=2.5 * osc(t, 16) * bump(t, 0, 0.45), ear_l=40 * bump(t, 0, 0.6) + 25 * bump(t, 1.0, 1.6),
                ear_r=40 * bump(t, 0, 0.6) + 25 * bump(t, 1.0, 1.6), look_x=0.9 * bump(t, 1.1, 1.8) * osc(t, 1.5),
                blink=0.7 * bump(t, 0.3, 0.6) + 0.4 * bump(t, 1.0, 1.3), tongue=0.4 * bump(t, 1.0, 1.7))

# 14 --------------------------------------------------------------- tail propeller
_TP_CACHE = {}
def _tp_spd(x):
    return 1500.0 * sm(seg(x, 0.2, 1.2)) * (1 - sm(seg(x, 2.4, 3.1)))

def _tp_scale():
    # scale the spin so the TOTAL rotation is an exact multiple of 360 deg -> lands perfectly at rest
    if 'k' not in _TP_CACHE:
        total = integ(_tp_spd, 3.6)
        _TP_CACHE['k'] = (360.0 * max(1, round(total / 360.0))) / total
    return _TP_CACHE['k']

@clip('tail_prop', 3.6, tags=('idle', 'playful', 'show'),
      events=[(0.3, 'sound', 'whirr'), (3.3, 'sound', 'land')],
      desc='Spins its tail like a propeller, lifts off dizzily, then wobbles back to the ground.')
def _tail_prop(t, o):
    k = _tp_scale()
    ang = (k * integ(_tp_spd, t)) % 360.0
    up = sm(seg(t, 0.2, 1.2)) * (1 - sm(seg(t, 2.6, 3.2)))
    s = _tp_spd(t) / 1500.0
    return dict(tail_rot=ang, tail_amp=-0.55 * up, lift=24 * sm(seg(t, 0.5, 1.5)) * (1 - sm(seg(t, 2.6, 3.2))),
                rot=-5.0 * s + 2 * osc(t, 1.1) * up, look_x=0.8 * up * osc(t, 2.2), look_y=0.5 * up * osc(t, 2.2, 1.57),
                blink=0.45 * up, tongue=0.4 * up, tongue_sway=0.8 * up * osc(t, 2.2),
                ear_l=-25 * up + 8 * osc(t, 9) * up, ear_r=-25 * up + 8 * osc(t, 9, 1) * up,
                sy=1 - 0.1 * bump(t, 3.1, 3.45), sx=1 + 0.07 * bump(t, 3.1, 3.45), shadow=1 - 0.4 * up)

# 15 --------------------------------------------------------------- idea!
@clip('idea_bulb', 2.9, tags=('idle', 'react', 'curious'),
      events=[(0.3, 'sound', 'ding'), fx(0.34, 'bulb', 0, -172, 0, -5, 2.0, 14),
              fx(0.34, 's', -50, -170, -40, -30, 0.7, 7), fx(0.34, 's', 50, -170, 40, -30, 0.7, 7)],
      desc='A startled hop, ears shoot up, a lightbulb pops above the head, then two satisfied nods.')
def _idea(t, o):
    sy = kf(t, [(0, 1), (0.1, 0.92), (0.3, 1.14, 'out_cubic'), (0.55, 0.92, 'in_cubic'), (0.85, 1.0, 'out_elastic'), (2.9, 1.0)])
    nod = (1 - sm(seg(t, 2.5, 2.9))) * sm(seg(t, 1.8, 2.0))
    return dict(sy=sy, sx=1 / math.sqrt(sy),
                lift=kf(t, [(0, 0), (0.1, -3), (0.3, 20, 'out_cubic'), (0.55, 0, 'in_cubic'), (0.7, 3, 'out_cubic'), (0.95, 0), (2.9, 0)]),
                ear_l=kf(t, [(0, 0), (0.3, 55, 'out_back'), (1.8, 50), (2.4, 5), (2.9, 0)]),
                ear_r=kf(t, [(0, 0), (0.3, 55, 'out_back'), (1.8, 50), (2.4, 5), (2.9, 0)]),
                look_y=kf(t, [(0, 0), (0.25, -1.0), (1.6, -1.0), (2.0, 0.15), (2.9, 0)]),
                head_dy=4 * max(0.0, osc(t, 1.9, -1.57)) * nod, tail_amp=0.9 * bump(t, 0.2, 1.4),
                tail_bend=0.5 * bump(t, 0.3, 1.2), tongue=0.2 * bump(t, 1.9, 2.8))

# 16 --------------------------------------------------------------- yawn + stretch + shake-off
@clip('yawn_stretch', 3.5, tags=('calm', 'sleepy', 'idle'),
      events=[(0.5, 'sound', 'yawn')],
      desc='Big full-body stretch with a trembling yawn, then a springy shake-off.')
def _yawn(t, o):
    st = sm(seg(t, 0.2, 1.3)) * (1 - sm(seg(t, 1.45, 1.8)))
    tau = max(0.0, t - 1.9)
    sh = decay(tau, 3.8) * (1 if t > 1.9 else 0)
    return dict(sy=1 + 0.12 * st - 0.05 * bump(t, 1.7, 2.1), sx=1 - 0.06 * st + 0.03 * bump(t, 1.7, 2.1), lift=6 * st,
                tongue=0.8 * st, blink=0.95 * st + 0.4 * bump(t, 2.4, 2.9), ear_l=-22 * st + 20 * sh * osc(tau, 4.5),
                ear_r=-22 * st + 20 * sh * osc(tau, 4.5, 0.6), head_dy=-4 * st, rot=0.8 * osc(t, 14) * bump(t, 0.9, 1.5) + 8 * sh * osc(tau, 4.0),
                wave=5 * sh, wave_k=0.05, wave_ph=-2 * math.pi * 3.0 * tau, tail_amp=0.4 * bump(t, 1.9, 3.0),
                tail_bend=0.5 * sh * osc(tau, 3.2))

# =============================================================================== PACK 2  (50 more clips)
def hs(n):                                   # deterministic hash noise in 0..1
    n = (int(n) * 1103515245 + 12345) & 0x7fffffff
    n = (n * 1103515245 + 12345) & 0x7fffffff
    return ((n >> 8) & 0xffff) / 65535.0

def env(t, a, b, fi=0.15, fo=0.2):           # smooth 0 -> 1 -> 0 plateau between a and b
    return sm(seg(t, a, a + fi)) * (1 - sm(seg(t, b - fo, b)))

def parab(t, t0, t1, h):                     # a hop arc of height h between t0 and t1
    if t1 <= t0 or t < t0 or t > t1: return 0.0
    u = (t - t0) / (t1 - t0)
    return 4 * h * u * (1 - u)

def vol(sy):                                 # volume-preserving partner for sy
    return 1.0 / math.sqrt(max(0.05, sy))

def steps(t, vals, rate, snap=0.25):         # stepped poses that snap quickly (robot / stop-motion feel)
    n = len(vals); i = int(t * rate); fr = (t * rate) - i
    a, b = vals[(i - 1) % n], vals[i % n]
    return a + (b - a) * sm(fr / snap)

def burst(t0, kind, n, speed, y=-60.0, life=1.1, size=8.0, seed=1, x=0.0, hold=0.5):
    ev = []
    for k in range(n):
        ang = 2 * math.pi * (k + 0.6 * hs(seed + k)) / n
        sp = speed * (0.7 + 0.6 * hs(seed * 3 + k))
        ev.append(fx(t0, kind, x, y, math.cos(ang) * sp, math.sin(ang) * sp, life, size * (0.8 + 0.5 * hs(seed * 5 + k)), 0.0, hold))
    return ev

# ------------------------------------------------------------------ 1 pogo
@clip('pogo_stick', 3.6, tags=('idle', 'playful'), events=[(0.2 + 0.62 * (k + 1), 'sound', 'boing') for k in range(5)],
      desc='Five pogo hops that shrink in height; stretch in the air, squash on every landing.')
def _pogo(t, o):
    T = 0.62; sy = 1 - 0.12 * bump(t, 0.0, 0.2); lift = air = land = 0.0
    i = int((t - 0.2) // T) if t >= 0.2 else -1
    if 0 <= i < 5:
        u = (t - 0.2 - i * T) / T; sc = 0.78 ** i
        lift = 4 * 34 * sc * u * (1 - u); air = math.sin(math.pi * u) * sc; sy += 0.08 * air
    for k in range(5):
        land += 0.78 ** k * bump(t, 0.2 + T * (k + 1) - 0.02, 0.2 + T * (k + 1) + 0.2)
    sy -= 0.2 * land
    return dict(lift=lift, sy=sy, sx=vol(sy), ear_l=-25 * air + 18 * land, ear_r=-25 * air + 18 * land,
                tail_amp=0.5 * air, blink=0.5 * land, tongue=0.2 * air)

# ------------------------------------------------------------------ 2 trampoline flip
@clip('trampoline_flip', 3.2, tags=('idle', 'show', 'playful'),
      events=[(0.85, 'sound', 'boing'), (1.45, 'sound', 'boing'), (2.4, 'sound', 'land'), fx(1.95, 's', 0, -140, 0, -25, 0.7, 9)],
      desc='Three bigger and bigger bounces ending in a full backflip.')
def _tramp(t, o):
    segs = ((0.35, 0.85, 12), (0.85, 1.45, 24), (1.45, 2.4, 48))
    lift = sum(parab(t, a, b, h) for a, b, h in segs)
    air = 1.0 if 0.35 < t < 2.4 and lift > 1 else 0.0
    land = 0.14 * bump(t, 0.83, 1.05) + 0.2 * bump(t, 1.43, 1.65) + 0.3 * bump(t, 2.38, 2.7)
    sy = 1 - 0.12 * bump(t, 0.08, 0.35) - land + 0.06 * air * min(1.0, lift / 20)
    flip = (-360.0 * sm(seg(t, 1.62, 2.22))) % 360.0
    return dict(lift=lift, sy=sy, sx=vol(sy), rot=flip, ear_l=-28 * air * min(1, lift / 25) + 20 * land,
                ear_r=-28 * air * min(1, lift / 25) + 20 * land, tail_bend=0.8 * bump(t, 1.6, 2.3),
                blink=0.5 * (land > 0.05), tongue=0.35 * bump(t, 1.6, 2.4))

# ------------------------------------------------------------------ 3 windy day
@clip('windy_day', 4.2, tags=('idle', 'calm'), desc='A gust leans him over: fluttering ears, streaming tail, squinting eyes, leaves whizzing past.',
      events=[fx(0.4 + 0.5 * k, 's', -190, -100 + 45 * k, 230, -10, 1.3, 6) for k in range(6)])
def _windy(t, o):
    A = env(t, 0, 4.2, 0.5, 0.7); g = A * (0.62 + 0.38 * osc(t, 0.6, -0.5))
    return dict(rot=-10 * g, dx=-11 * g, ear_l=26 * g + 15 * g * osc(t, 11), ear_r=26 * g + 15 * g * osc(t, 11, 1.3),
                tail_bend=1.0 * g, tail_amp=1.0 * g, blink=0.55 * g, tongue=0.15 * g, wave=3.2 * g, wave_k=0.06,
                wave_ph=-2 * math.pi * 3.0 * t, head_rot=-4 * g, sx=1 - 0.04 * g)

# ------------------------------------------------------------------ 4 tipsy
@clip('tipsy_stumble', 4.0, tags=('idle', 'playful'), desc='Wobbly Lissajous stumble: loose head, drooping eyes, swirling gaze.')
def _tipsy(t, o):
    A = env(t, 0, 4.0, 0.4, 0.6)
    return dict(dx=(14 * osc(t, 0.5) + 7 * osc(t, 1.3)) * A, rot=(9 * osc(t, 0.5, 0.6) + 4 * osc(t, 1.3, 1.1)) * A,
                lift=3 * osc(t, 1.0) * A, head_rot=-8 * osc(t, 0.5, 1.4) * A, look_x=0.9 * osc(t, 0.9) * A,
                look_y=0.6 * osc(t, 0.9, 1.57) * A, blink=0.4 * A, tongue=0.55 * A, tongue_sway=0.8 * osc(t, 0.5) * A,
                ear_l=14 * osc(t, 0.7) * A, ear_r=14 * osc(t, 0.7, 2.0) * A, tail_bend=0.9 * osc(t, 0.5, 0.3) * A, tail_amp=0.4 * A)

# ------------------------------------------------------------------ 5 skydiver
@clip('skydiver', 3.4, tags=('idle', 'playful', 'show'),
      desc='Belly-down freefall pose: flattened body, fluttering ears, flapping tongue, shimmering wind.')
def _sky(t, o):
    A = env(t, 0, 3.4, 0.4, 0.5)
    return dict(sx=1 + 0.16 * A, sy=1 - 0.13 * A, lift=6 * osc(t, 2.8) * A + 6 * A, ear_l=10 * A * osc(t, 9) + 8 * A,
                ear_r=10 * A * osc(t, 9, 1.0) + 8 * A, tail_bend=-1.0 * A, tail_amp=1.1 * A, tongue=(0.7 + 0.25 * osc(t, 10)) * A,
                tongue_sway=0.5 * osc(t, 8) * A, blink=0.55 * A, look_y=-0.3 * A, wave=2.8 * A, wave_k=0.12,
                wave_ph=-2 * math.pi * 6 * t, shadow=1 - 0.5 * A, rot=2 * osc(t, 1.7) * A)

# ------------------------------------------------------------------ 6 ceiling bonk
@clip('ceiling_bonk', 2.9, tags=('idle', 'playful', 'react'),
      events=[(0.45, 'sound', 'bonk'), fx(0.46, 'star', -38, -172, -14, -3, 1.4, 8), fx(0.46, 'star', 38, -170, 14, -3, 1.4, 7),
              fx(0.46, 'star', 0, -190, 0, -6, 1.4, 9)],
      desc='Jumps, bonks an invisible ceiling, flattens, drops, and sees stars.')
def _bonk(t, o):
    lift = kf(t, [(0, 0), (0.18, -4), (0.4, 44, 'out_cubic'), (0.5, 44), (0.95, 0, 'in_cubic'), (1.05, 2), (1.2, 0)])
    sy = kf(t, [(0, 1), (0.18, 0.88), (0.4, 1.22, 'out_cubic'), (0.46, 0.7, 'in_cubic'), (0.6, 1.0, 'out_back'),
                (0.93, 1.1), (1.03, 0.84, 'out_cubic'), (1.4, 1.0, 'out_elastic'), (2.9, 1.0)])
    dz = bump(t, 1.1, 2.6)
    return dict(lift=lift, sy=sy, sx=vol(sy), look_x=0.8 * osc(t, 1.4) * dz, look_y=0.4 * osc(t, 1.4, 1.5) * dz, blink=0.8 * dz * 0.5 + 0.7 * bump(t, 0.44, 0.7),
                head_rot=9 * osc(t, 1.4) * dz, tongue=0.5 * dz, ear_l=-30 * bump(t, 0.44, 1.0) + 8 * osc(t, 2, 1) * dz, ear_r=-30 * bump(t, 0.44, 1.0) + 8 * osc(t, 2) * dz,
                rot=4 * osc(t, 1.4, 0.5) * dz)

# ------------------------------------------------------------------ 7 wall ping-pong
@clip('wall_pingpong', 3.4, tags=('idle', 'playful'), lag={'ear_l': (80, 9), 'ear_r': (80, 9)},
      events=[((0.25 + 0.5 * k) / 0.55, 'sound', 'tick') for k in range(1, 5)],
      desc='Bounces off invisible walls left and right, squashing flat on every hit.')
def _pingpong(t, o):
    f = 0.55; A = env(t, 0, 3.4, 0.25, 0.5); ph = 2 * math.pi * f * t
    tri = (2 / math.pi) * math.asin(math.sin(ph)); vel = math.cos(ph)
    u = (t * f + 0.25) % 0.5 / 0.5; near = min(u, 1 - u)
    sq = sm(1 - near / 0.14) if near < 0.14 else 0.0
    return dict(dx=34 * A * tri, rot=8 * A * vel, sx=1 - 0.22 * sq * A, sy=1 + 0.12 * sq * A, lift=5 * A * (1 - vel * vel),
                look_x=0.9 * A * (1 if vel > 0 else -1) * min(1, abs(vel) * 3), ear_l=-14 * A * vel, ear_r=-14 * A * vel,
                tail_bend=-0.8 * A * vel, blink=0.4 * sq * A)

# ------------------------------------------------------------------ 8 tumbleweed
@clip('tumbleweed', 3.2, tags=('idle', 'playful'), desc='Curls up into a ball and rolls out and back like a tumbleweed.')
def _tumble(t, o):
    c = sm(seg(t, 0.15, 0.6)) * (1 - sm(seg(t, 2.5, 3.0)))
    out = sm(seg(t, 0.4, 1.4)); back = sm(seg(t, 1.6, 2.6)); dx = 42 * (out - back)
    s = 1 - 0.36 * c
    return dict(sx=s, sy=s, dx=dx, rot=(7.5 * dx) % 360.0, lift=-(1 - s) * 150 + 4 * abs(osc(t, 2.4)) * c,
                ear_l=-45 * c, ear_r=-45 * c, tail_amp=-0.5 * c, blink=1.0 * c, shadow=1.0)

# ------------------------------------------------------------------ 9 shy turn
@clip('shy_turn', 3.4, tags=('emotion', 'affection', 'calm'), events=[fx(2.0, 'heart', 62, -128, 4, -14, 1.4, 7)],
      desc='Gets bashful: shrinks, turns away, sneaks a peek back, then looks away again.')
def _shy(t, o):
    A = env(t, 0, 3.4, 0.5, 0.6); peek = bump(t, 1.7, 2.5)
    return dict(sx=1 - 0.09 * A, sy=1 - 0.05 * A, rot=-6 * A, head_rot=-10 * A + 12 * peek, head_dy=5 * A, look_x=-1.0 * A + 1.7 * peek,
                ear_l=-22 * A + 40 * peek, ear_r=-22 * A + 40 * peek, tail_amp=-0.4 * A, tail_rot=14 * A, blink=0.5 * A * (1 - peek))

# ------------------------------------------------------------------ 10 sulk
@clip('sulk', 3.8, tags=('emotion', 'calm'), events=[(0.4, 'sound', 'hmph'), fx(0.45, 'ring', 0, -40, 0, 0, 0.7, 14),
                                                      fx(0.4, 'drop', 78, -100, 6, 12, 1.0, 5, 0.0)],
      desc='Slumps with a pout: low, wide, ears down, side-eyed and grumbling.')
def _sulk(t, o):
    A = env(t, 0.1, 3.8, 0.6, 0.7)
    return dict(lift=-5 * A, sy=1 - 0.1 * A, sx=1 + 0.08 * A, ear_l=-38 * A, ear_r=-38 * A, tail_amp=-0.5 * A, head_dy=6 * A,
                blink=0.55 * A, look_x=-0.9 * A * sm(seg(t, 0.6, 1.4)), rot=0.7 * osc(t, 9) * bump(t, 0.3, 1.2) * A,
                breathe=0.05 * bump(t, 1.3, 2.3), tongue=-0.2 * A)

# ------------------------------------------------------------------ 11 giggle fit
@clip('giggle_fit', 3.0, tags=('emotion', 'playful', 'idle'),
      events=[fx(0.3 + 0.55 * k, 's', (-1) ** k * 72, -110, (-1) ** k * 30, -30, 0.6, 6) for k in range(5)],
      desc='Uncontrollable giggles: fast shakes, tiny hops, squeezed eyes, flapping ears.')
def _giggle(t, o):
    A = env(t, 0, 3.0, 0.25, 0.5)
    return dict(lift=5 * abs(osc(t, 6)) * A, sy=1 + 0.06 * osc(t, 12) * A, sx=1 - 0.04 * osc(t, 12) * A, rot=3.5 * osc(t, 6.5) * A,
                ear_l=24 * osc(t, 6) * A + 8 * A, ear_r=24 * osc(t, 6, 3.14) * A + 8 * A, blink=0.7 * A, tongue=0.5 * A,
                tail_amp=1.0 * A, head_rot=4 * osc(t, 3.2) * A)

# ------------------------------------------------------------------ 12 jumpscare
@clip('jumpscare', 2.6, tags=('emotion', 'react'), fade=(0.015, 0.25),
      events=[(0.06, 'sound', 'gasp'), fx(0.08, 'bang', 0, -178, 0, -6, 1.4, 13), fx(0.08, 'ring', 0, -20, 0, 0, 0.5, 22)],
      desc='Instant startle: shoots tall, ears rigid, tail frizzed, then a long trembling recovery.')
def _scare(t, o):
    sy = kf(t, [(0, 1), (0.06, 1.24, 'out_cubic'), (0.35, 1.1), (0.9, 1.0), (2.6, 1.0)])
    sh = decay(max(0, t - 0.1), 1.6) * (1 if t > 0.1 else 0)
    return dict(sy=sy, sx=vol(sy), lift=kf(t, [(0, 0), (0.08, 26, 'out_cubic'), (0.4, 0, 'in_cubic'), (0.5, 3), (0.65, 0)]),
                ear_l=60 * min(1, t / 0.08) * (1 - sm(seg(t, 1.6, 2.4))), ear_r=60 * min(1, t / 0.08) * (1 - sm(seg(t, 1.6, 2.4))),
                tail_amp=1.4 * sh, tail_phase=2 * osc(t, 18) * sh, rot=2.5 * osc(t, 25) * sh, dx=1.5 * osc(t, 31) * sh,
                look_y=-0.4 * sh, blink=0.0)

# ------------------------------------------------------------------ 13 confused tilt
@clip('confused_tilt', 3.6, tags=('emotion', 'curious', 'idle'),
      events=[fx(0.5, 'quest', 70, -150, 6, -12, 1.8, 11), fx(1.9, 'quest', -66, -148, -6, -12, 1.8, 10)],
      desc='Puzzled head-tilts left then right with scanning eyes and a question mark.')
def _confused(t, o):
    A = env(t, 0, 3.6, 0.3, 0.5)
    tilt = hermite(t, [(0, 0), (0.7, 16), (1.5, 14), (2.0, -16), (2.9, -14), (3.6, 0)])
    return dict(head_rot=tilt * A, rot=tilt * 0.15 * A, look_x=-0.8 * (tilt / 16) * A, look_y=-0.2 * A,
                ear_l=22 * max(0, tilt / 16) * A - 8 * A, ear_r=22 * max(0, -tilt / 16) * A - 8 * A, blink=0.9 * bump(t, 1.6, 1.85), tail_amp=0.3 * A)

# ------------------------------------------------------------------ 14 panting heat
@clip('panting_heat', 3.6, tags=('emotion', 'calm'),
      events=[fx(0.6 + 0.7 * k, 'drop', (-1) ** k * 80, -118, (-1) ** k * 6, 34, 1.3, 6) for k in range(4)],
      desc='Overheated: big panting tongue, wilted ears, heat shimmer and sweat drops.')
def _pant(t, o):
    A = env(t, 0, 3.6, 0.5, 0.7)
    return dict(tongue=(1.05 + 0.25 * osc(t, 5)) * A, sy=1 + 0.03 * osc(t, 5) * A, sx=1 - 0.02 * osc(t, 5) * A, head_dy=2 * A,
                ear_l=-32 * A, ear_r=-32 * A, blink=0.45 * A, tail_amp=-0.4 * A, wave=1.8 * A, wave_k=0.09, wave_ph=-2 * math.pi * 1.4 * t)

# ------------------------------------------------------------------ 15 diva
@clip('diva_sparkle', 3.6, tags=('idle', 'show', 'playful'),
      events=[fx(0.4 + 0.3 * k, 'star', 105 * math.cos(2.4 * k + 0.5), -60 + 95 * math.sin(2.4 * k + 0.5), 0, -6, 0.9, 8 + (k % 3) * 2) for k in range(9)],
      desc='Slow diva pose with a tilted head, V-ears and twinkling stars around him.')
def _diva(t, o):
    A = env(t, 0.1, 3.6, 0.5, 0.6)
    return dict(head_rot=8 * A, rot=3 * A + 2 * osc(t, 0.4) * A, ear_l=34 * A, ear_r=34 * A, blink=0.38 * A, lift=4 * osc(t, 0.9) * A + 4 * A,
                breathe=0.012 * osc(t, 0.8) * A, tail_amp=0.4 * A, tail_bend=0.5 * A)

# ------------------------------------------------------------------ 16 tantrum stomp
@clip('tantrum_stomp', 3.2, tags=('emotion', 'react'),
      events=[(0.3 + 0.45 * k + 0.45, 'sound', 'land') for k in range(5)] +
             [fx(0.75 + 0.45 * k, 'ring', 0, 135, 0, 0, 0.45, 12) for k in range(5)] + [fx(0.5, 'bang', 70, -150, 4, -10, 1.0, 9)],
      desc='Furious little stomps with shuddering landings, squinting eyes and a whipping tail.')
def _tantrum(t, o):
    A = env(t, 0, 3.2, 0.2, 0.5)
    lift = sum(parab(t, 0.3 + 0.45 * k, 0.75 + 0.45 * k, 9) for k in range(5)) * A
    hit = sum(bump(t, 0.75 + 0.45 * k, 0.75 + 0.45 * k + 0.22) for k in range(5)) * A
    return dict(lift=lift, sy=1 - 0.13 * hit, sx=1 + 0.09 * hit, rot=5 * osc(t, 14) * hit, ear_l=-18 * A, ear_r=-18 * A, blink=0.7 * A,
                tail_amp=1.5 * A, tail_phase=osc(t, 9) * A, head_dy=3 * hit)

# ------------------------------------------------------------------ 17 swoon
@clip('swoon', 3.8, tags=('emotion', 'affection'),
      events=[fx(0.8 + 0.5 * k, 'heart', (-1) ** k * 34, -150, (-1) ** k * 10, -22, 1.8, 8 + k) for k in range(5)],
      desc='Swoons backward in bliss: eyes closed, hearts rising, then a springy recovery.')
def _swoon(t, o):
    rot = kf(t, [(0, 0), (0.9, -23, 'out_cubic'), (2.4, -20), (3.1, 2, 'out_back'), (3.8, 0)])
    A = env(t, 0, 3.8, 0.5, 0.6)
    return dict(rot=rot, lift=8 * bump(t, 0.4, 3.2), sy=1 - 0.04 * A, head_rot=-10 * A, blink=1.0 * bump(t, 0.5, 3.0), tongue=0.2 * A,
                ear_l=22 * A, ear_r=22 * A, tail_amp=0.6 * A, tail_bend=0.5 * A, breathe=0.015 * osc(t, 0.9) * A)

# ------------------------------------------------------------------ 18 doze nod
@clip('doze_nod', 4.4, tags=('sleepy', 'calm'), events=[fx(3.6, 'z', 58, -150, 12, -16, 2.2, 8), fx(0.95, 'bang', 62, -140, 0, -8, 0.6, 7)],
      desc='Nodding off at the desk: head sinks, jerks awake twice, then finally dozes.')
def _doze(t, o):
    hd = kf(t, [(0, 0), (1.0, 11), (1.08, -4, 'out_cubic'), (1.3, 0), (1.5, 0), (2.4, 12), (2.5, -4, 'out_cubic'), (2.8, 0), (3.0, 0), (3.9, 13), (4.4, 0)])
    k = clamp(hd / 9.0, 0.0, 1.0)
    return dict(head_dy=hd, blink=1.0 * k, rot=6 * hd / 12.0, ear_l=-15 * k + 30 * max(0, -hd / 4), ear_r=-15 * k + 30 * max(0, -hd / 4),
                tail_amp=-0.3 * k, breathe=0.01 * osc(t, 0.6))

# ------------------------------------------------------------------ 19 radar sweep
@clip('radar_sweep', 3.8, tags=('idle', 'curious', 'calm'),
      events=[fx(0.15 + 0.909 * k + (0.45 if j else 0), 'ring', 118 * (-1) ** j, -92, 0, 0, 0.95, 10) for k in range(4) for j in (0, 1)],
      desc='Ears pulse alternately like radar dishes while the eyes sweep; sonar rings ripple out.')
def _radar(t, o):
    A = env(t, 0, 3.8, 0.4, 0.5)
    return dict(ear_l=30 * max(0, osc(t, 1.1)) * A, ear_r=30 * max(0, osc(t, 1.1, math.pi)) * A, look_x=0.95 * osc(t, 0.55) * A,
                head_rot=5 * osc(t, 0.55) * A, blink=0.1 * A, tail_amp=0.2 * A)

# ------------------------------------------------------------------ 20 groove
@clip('groove', 4.0, tags=('idle', 'playful', 'music'),
      events=[fx(0.3 + 0.5 * k, 'note', (-1) ** k * 66, -130, (-1) ** k * 12, -30, 1.7, 8 + (k % 2) * 2) for k in range(7)],
      desc='Grooves to an imaginary beat: bobbing head, alternating ears, wagging tail, floating notes.')
def _groove(t, o):
    A = env(t, 0, 4.0, 0.4, 0.5); b = osc(t, 2.0)
    return dict(lift=4 * abs(osc(t, 1.0)) * A, head_rot=8 * osc(t, 1.0) * A, rot=4 * osc(t, 1.0, 0.5) * A, sy=1 + 0.05 * (b * b) * A,
                ear_l=20 * osc(t, 2.0) * A, ear_r=20 * osc(t, 2.0, 3.14) * A, tail_amp=1.0 * A, tail_rot=10 * osc(t, 1.0) * A,
                tongue=0.2 * A, blink=0.5 * A)

# ------------------------------------------------------------------ 21 slingshot
@clip('slingshot', 2.9, tags=('idle', 'playful', 'show'), events=[(1.3, 'sound', 'whoosh'), fx(1.35, 'ring', -20, 0, 120, 0, 0.5, 14)],
      desc='Gets pulled back like a slingshot, quivers, then fires across the window and settles.')
def _sling(t, o):
    dx = kf(t, [(0, 0), (0.9, -34), (1.3, -35), (1.45, 30, 'out_cubic'), (1.95, -6), (2.3, 3), (2.7, 0), (2.9, 0)])
    pull = sm(seg(t, 0, 0.9)) * (1 - sm(seg(t, 1.25, 1.45))); rel = bump(t, 1.3, 2.0)
    return dict(dx=dx, sx=1 + 0.25 * pull - 0.22 * rel, sy=1 - 0.15 * pull + 0.2 * rel, rot=10 * pull - 12 * rel, blink=0.9 * pull,
                ear_l=-20 * pull + 40 * rel, ear_r=-20 * pull + 40 * rel, tail_bend=-0.8 * rel + 0.6 * pull)

# ------------------------------------------------------------------ 22 jack in the box
@clip('jack_in_box', 2.8, tags=('idle', 'show', 'react'),
      events=[(1.2, 'sound', 'boing'), fx(1.24, 'bang', 0, -190, 0, -6, 1.0, 12), fx(1.24, 'ring', 0, -30, 0, 0, 0.5, 26)],
      desc='Squashes down tensely like a coiled spring, shudders, and pops up with a bang.')
def _jack(t, o):
    sy = kf(t, [(0, 1), (0.5, 0.55), (1.2, 0.5), (1.26, 1.45, 'out_cubic'), (1.5, 0.9, 'in_cubic'), (1.9, 1.06, 'out_back'), (2.3, 1.0), (2.8, 1.0)])
    lift = kf(t, [(0, 0), (0.5, -26), (1.2, -28), (1.26, 18, 'out_cubic'), (1.7, 0), (2.8, 0)])
    pop = kf(t, [(0, 0), (1.2, 0), (1.26, 70, 'out_cubic'), (1.7, 25, 'out_elastic'), (2.4, 0), (2.8, 0)])
    return dict(sy=sy, sx=vol(sy), lift=lift, ear_l=-50 * sm(seg(t, 0.1, 0.6)) * (1 if t < 1.2 else 0) + pop, ear_r=-50 * sm(seg(t, 0.1, 0.6)) * (1 if t < 1.2 else 0) + pop,
                rot=2 * osc(t, 22) * bump(t, 0.8, 1.2), tongue=0.6 * bump(t, 1.2, 1.9), blink=0.8 * bump(t, 0.2, 1.2), tail_amp=1.0 * bump(t, 1.2, 2.0))

# ------------------------------------------------------------------ 23 wind-up toy
@clip('windup_toy', 4.6, tags=('idle', 'show', 'playful'),
      events=[(0.3 + 0.2 * i, 'sound', 'tick') for i in range(8)],
      desc='A key ratchets on his tail, then he waddles like a wind-up toy until the spring runs down.')
def _windup(t, o):
    wind = sum(45.0 * sm(seg(t, 0.3 + 0.2 * i, 0.3 + 0.2 * i + 0.08)) for i in range(8))
    W = sm(seg(t, 2.0, 2.4)) * (1 - sm(seg(t, 3.4, 4.5))); run = W * (0.35 + 0.65 * (1 - sm(seg(t, 2.4, 4.5))))
    f = 2.8 * (0.5 + 0.5 * (1 - sm(seg(t, 2.4, 4.5))))
    return dict(tail_rot=wind % 360.0, rot=8 * osc(t, f) * W, dx=11 * osc(t, f / 2) * W, lift=3.5 * abs(osc(t, f)) * W,
                ear_l=14 * osc(t, f, 1) * W + 4 * sm(seg(t, 0.3, 0.6)) * (1 - W), ear_r=14 * osc(t, f, 1) * W, blink=0.2 * W, shadow=1.0)

# ------------------------------------------------------------------ 24 stop motion
@clip('stop_motion', 3.6, tags=('idle', 'show', 'playful'), desc='Hand-made claymation look: poses held at 8 fps with little jittery pops between.')
def _stopmo(t, o):
    A = env(t, 0, 3.6, 0.3, 0.4); n = int(t * 8)
    r = lambda k: hs(n * 7 + k) - 0.5
    return dict(rot=r(1) * 14 * A, dx=r(2) * 14 * A, lift=abs(r(3)) * 12 * A, head_rot=r(4) * 16 * A, ear_l=r(5) * 40 * A + 6 * A,
                ear_r=r(6) * 40 * A + 6 * A, tail_bend=r(7) * 2 * A, sy=1 + r(8) * 0.12 * A, sx=1 + r(9) * 0.1 * A, blink=0.9 * A * (hs(n * 3) > 0.82))

# ------------------------------------------------------------------ 25 glitch
@clip('glitch_out', 2.8, tags=('idle', 'show'), events=[(0.2, 'sound', 'charge')],
      desc='Digital glitch: sliced warps, stretching flashes, flickering eyes and jittery jumps.')
def _glitch(t, o):
    A = env(t, 0.1, 2.7, 0.1, 0.3); n = int(t * 24)
    on = 1.0 if hs(n * 5 + 2) > 0.58 else 0.0; g = on * A
    return dict(dx=(hs(n * 3 + 1) - 0.5) * 38 * g, sx=1 + (hs(n * 3 + 2) - 0.5) * 0.8 * g, sy=1 + (hs(n * 3 + 4) - 0.5) * 0.2 * g,
                wave=15 * g, wave_k=0.2 + 0.1 * hs(n), wave_ph=hs(n * 11) * 6.28, blink=0.9 * g * (hs(n * 13) > 0.5),
                look_x=(hs(n * 17) - 0.5) * 2 * g, rot=(hs(n * 19) - 0.5) * 10 * g, tongue=0.6 * g * (hs(n * 23) > 0.6))

# ------------------------------------------------------------------ 26 periscope
@clip('periscope', 3.8, tags=('idle', 'curious', 'playful'), desc='Stretches the head up on a long neck like a periscope and scans the horizon.')
def _peri(t, o):
    up = kf(t, [(0, 0), (0.5, -22, 'out_back'), (3.0, -22), (3.6, 0, 'smooth'), (3.8, 0)]); k = -up / 22.0
    return dict(head_dy=up, look_x=1.0 * osc(t, 0.5) * k, head_rot=10 * osc(t, 0.5, 0.4) * k, ear_l=16 * k, ear_r=16 * k,
                blink=0.9 * bump(t, 1.7, 1.95), tail_amp=0.2 * k, sy=1 + 0.02 * k)

# ------------------------------------------------------------------ 27 inflatable tube dancer
@clip('tube_dancer', 3.8, tags=('idle', 'show', 'playful'), desc='Becomes a wacky inflatable tube man: the whole body ripples and whips.')
def _tube(t, o):
    A = env(t, 0, 3.8, 0.4, 0.6)
    return dict(wave=15 * A, wave_k=0.055, wave_ph=-2 * math.pi * 1.8 * t, sx=1 + 0.05 * osc(t, 3.6) * A, sy=1 + 0.05 * osc(t, 3.6, 1.5) * A,
                dx=8 * osc(t, 0.9) * A, ear_l=40 * osc(t, 1.8) * A, ear_r=40 * osc(t, 1.8, 1.2) * A, tail_bend=osc(t, 1.8, 2.0) * A, tail_amp=1.0 * A,
                blink=0.3 * A, tongue=0.4 * A, lift=5 * osc(t, 1.8, 0.7) * A)

# ------------------------------------------------------------------ 28 zen breath
@clip('zen_breath', 5.0, tags=('calm', 'idle', 'sleepy'),
      events=[fx(2.0, 'ring', 0, -150, 0, 0, 1.9, 12, 0.0, 0.3, 2.2), fx(4.2, 'ring', 0, -150, 0, 0, 1.9, 12, 0.0, 0.3, 2.2)],
      desc='Two slow meditative breaths, eyes closed, calm ripples drifting off the head.')
def _zen(t, o):
    A = env(t, 0, 5.0, 0.6, 0.7); b = 0.5 - 0.5 * math.cos(2 * math.pi * t / 2.4)
    return dict(breathe=0.065 * b * A, sy=1 + 0.03 * b * A, lift=5 * b * A, ear_l=14 * b * A, ear_r=14 * b * A, blink=1.0 * A,
                head_dy=-2 * b * A, tail_amp=-0.3 * A)

# ------------------------------------------------------------------ 29 applause
@clip('applause', 3.2, tags=('idle', 'playful', 'affection'),
      events=[fx(0.3 + 0.22 * k, 's', (-1) ** k * 124, -96, (-1) ** k * 28, -26, 0.55, 6) for k in range(10)],
      desc='Ears clap in rapid alternation with happy hops and sparks flying off the tips.')
def _applause(t, o):
    A = env(t, 0, 3.2, 0.25, 0.4)
    return dict(ear_l=30 * osc(t, 5) * A + 12 * A, ear_r=30 * osc(t, 5, 3.14) * A + 12 * A, lift=5 * abs(osc(t, 2.5)) * A, blink=0.5 * A,
                tongue=0.3 * A, sy=1 + 0.03 * osc(t, 5) * A, tail_amp=0.9 * A)

# ------------------------------------------------------------------ 30 static shock
@clip('static_shock', 3.2, tags=('emotion', 'react', 'show'),
      events=[fx(0.1 + 0.12 * k, 'zap', 150 * math.cos(k * 2.2), -30 + 110 * math.sin(k * 2.2), 0, 0, 0.2, 9 + (k % 3)) for k in range(11)] +
             [(0.1, 'sound', 'zap'), fx(1.5, 'ring', 0, -150, 0, -14, 1.2, 8), fx(1.8, 'ring', 8, -158, 0, -14, 1.2, 7)],
      desc='Zapped by static: vibrating, ears rigid, eyes flickering, lightning bolts, then a singed slump.')
def _shock(t, o):
    A = sm(seg(t, 0, 0.1)) * (1 - sm(seg(t, 1.3, 1.5))); S2 = bump(t, 1.4, 3.2)
    return dict(rot=2.8 * osc(t, 28) * A + 1.2 * osc(t, 3) * S2, dx=2.2 * osc(t, 33) * A, ear_l=58 * A - 22 * S2, ear_r=58 * A - 22 * S2,
                tail_amp=1.5 * A, tail_phase=osc(t, 19) * A, blink=0.95 * A * (1 if osc(t, 12) > 0 else 0) + 0.4 * S2, look_x=osc(t, 7) * A,
                tongue=0.8 * S2, sy=1 - 0.07 * S2, head_dy=3 * S2, wave=2.5 * A, wave_k=0.15, wave_ph=osc(t, 40) * 3)

# ------------------------------------------------------------------ 31 biscuit kneading
@clip('biscuit_kneading', 3.8, tags=('affection', 'calm', 'idle'), events=[fx(1.5, 'heart', 40, -150, 6, -18, 1.6, 7)],
      desc='Makes biscuits: ears paddle in alternation, eyes in happy arcs, a faint purr-rumble.')
def _knead(t, o):
    A = env(t, 0, 3.8, 0.4, 0.6)
    return dict(ear_l=20 * osc(t, 1.8) * A, ear_r=20 * osc(t, 1.8, 3.14) * A, lift=2.2 * abs(osc(t, 1.8)) * A, blink=0.9 * A,
                rot=0.8 * osc(t, 16) * A, tongue=0.15 * A, tail_amp=0.7 * A, sy=1 + 0.025 * osc(t, 3.6) * A, head_dy=2 * osc(t, 1.8) * A)

# ------------------------------------------------------------------ 32 slow blink
@clip('slow_blink', 2.6, tags=('affection', 'calm', 'idle'), events=[fx(1.0, 'heart', 46, -150, 4, -16, 1.4, 6)],
      desc='A long, trusting cat-style slow blink with a gentle head tilt.')
def _slowblink(t, o):
    return dict(blink=1.0 * bump(t, 0.2, 1.7), head_rot=8 * bump(t, 0.1, 2.3), ear_l=10 * bump(t, 0.2, 2.2), ear_r=10 * bump(t, 0.2, 2.2),
                tail_amp=0.4 * bump(t, 0.5, 2.3), rot=1.5 * bump(t, 0.1, 2.3))

# ------------------------------------------------------------------ 33 zoomies  (travel)
_ZM = [(0.35, -200, -30, 0.2), (0.62, 260, 20, 0.22), (0.9, -170, -50, 0.2), (1.15, 230, 40, 0.2), (1.42, -90, -20, 0.16)]
@clip('zoomies', 2.9, tags=('idle', 'playful', 'travel'), travel=True, cooldown=40,
      events=[(0.0, 'mark', {})] + [(a, 'move', dict(dx=b, dy=c, dur=d, ease='smooth')) for a, b, c, d in _ZM] +
             [(a, 'sound', 'whoosh') for a, b, c, d in _ZM] + [fx(a + d, 'ring', (150 if b > 0 else -150), 30, 0, 0, 0.4, 14) for a, b, c, d in _ZM] +
             [(1.8, 'pop', dict(dur=0.7, ease='smooth'))],
      desc='A frantic zig-zag of dashes across the screen with hairpin turns, then a calm return.')
def _zoomies(t, o):
    lean = sum((1 if b > 0 else -1) * 14 * bump(t, a - 0.05, a + d + 0.15) for a, b, c, d in _ZM)
    ex = sum(bump(t, a - 0.04, a + d + 0.1) for a, b, c, d in _ZM)
    return dict(rot=lean, sx=1 + 0.1 * min(1, ex), sy=1 - 0.07 * min(1, ex), ear_l=-35 * min(1, ex), ear_r=-35 * min(1, ex),
                tail_bend=-0.9 * (1 if lean > 0 else -1) * min(1, ex), tail_amp=1.0 * min(1, ex), blink=0.4 * min(1, ex), tongue=0.5 * min(1, ex))

# ------------------------------------------------------------------ 34 loop the loop (travel)
_N = 26; _R = 85.0
def _lp(a): return (_R * math.sin(a), -_R * (1 - math.cos(a)))
_LOOP = [(0.0, 'mark', {})]
for _i in range(_N):
    _a0, _a1 = 2 * math.pi * sm(_i / _N), 2 * math.pi * sm((_i + 1) / _N)
    _p0, _p1 = _lp(_a0), _lp(_a1)
    _LOOP.append((0.5 + 1.3 * _i / _N, 'move', dict(dx=_p1[0] - _p0[0], dy=_p1[1] - _p0[1], dur=1.3 / _N, ease='lin')))
_LOOP += [(0.5, 'sound', 'whoosh'), (1.9, 'pop', dict(dur=0.4, ease='smooth'))]
@clip('loop_the_loop', 3.0, tags=('idle', 'show', 'travel'), travel=True, cooldown=45, events=_LOOP,
      desc='Flies a full aerobatic loop-the-loop through the air, rotating along the path.')
def _loop(t, o):
    s = sm(seg(t, 0.5, 1.8)); a = 1.0 if 0.5 < t < 1.8 else 0.0
    return dict(rot=(360.0 * s) % 360.0, ear_l=-40 * a, ear_r=-40 * a, tail_amp=0.9 * a, blink=0.5 * a, tongue=0.5 * a,
                sy=1 + 0.08 * bump(t, 0.5, 1.8), sx=1 - 0.05 * bump(t, 0.5, 1.8), shadow=1 - 0.6 * a)

# ------------------------------------------------------------------ 35 yoyo (travel)
@clip('yoyo', 3.2, tags=('idle', 'playful', 'travel'), travel=True, cooldown=40,
      events=[(0.0, 'mark', {}), (0.4, 'move', dict(dx=0, dy=-170, dur=0.5, ease='out_cubic')), (0.4, 'sound', 'whoosh'),
              (1.3, 'pop', dict(dur=1.2, ease='out_elastic')), (1.45, 'sound', 'boing')],
      desc='Shoots up, then drops back on an elastic string with a spin and a springy overshoot.')
def _yoyo(t, o):
    sy = 1 - 0.14 * bump(t, 0.1, 0.4) + 0.18 * bump(t, 0.4, 0.95) + 0.16 * bump(t, 1.3, 1.9) - 0.1 * bump(t, 2.0, 2.5)
    return dict(sy=sy, sx=vol(sy), rot=(360.0 * sm(seg(t, 1.3, 2.2))) % 360.0, ear_l=-30 * bump(t, 0.4, 1.0) + 40 * bump(t, 1.3, 2.0),
                ear_r=-30 * bump(t, 0.4, 1.0) + 40 * bump(t, 1.3, 2.0), blink=0.5 * bump(t, 1.2, 2.2), tongue=0.4 * bump(t, 1.3, 2.2), tail_amp=0.8 * bump(t, 0.3, 2.3))

# ------------------------------------------------------------------ 36 bubble blow
@clip('bubble_blow', 3.6, tags=('idle', 'playful', 'show'),
      events=[fx(0.6, 'ring', 0, -42, 0, -8, 2.0, 16, 0.0, 0.97, 2.3), (2.6, 'sound', 'pop')] + burst(2.62, 's', 7, 120, -75, 0.6, 7, 3) +
             [fx(2.62, 'ring', 0, -75, 0, 0, 0.4, 30)],
      desc='Blows a big gum-bubble from the tongue, then it pops and sticks to his face.')
def _bubble(t, o):
    tongue = kf(t, [(0, 0), (0.5, 0.2), (2.55, 0.85, 'smooth'), (2.62, -0.1, 'out_cubic'), (3.0, 0.0, 'out_elastic'), (3.6, 0)])
    sy = 1 - 0.08 * bump(t, 2.6, 3.2) + 0.03 * sm(seg(t, 0.6, 2.5)) * (1 - sm(seg(t, 2.55, 2.62)))
    return dict(tongue=tongue, sy=sy, sx=vol(sy), blink=0.6 * sm(seg(t, 0.8, 2.4)) * (1 - sm(seg(t, 2.55, 2.7))) + 0.9 * bump(t, 2.6, 3.1),
                ear_l=10 * sm(seg(t, 0.8, 2.5)) - 30 * bump(t, 2.6, 3.2), ear_r=10 * sm(seg(t, 0.8, 2.5)) - 30 * bump(t, 2.6, 3.2),
                lift=3 * sm(seg(t, 0.8, 2.5)) * (1 - sm(seg(t, 2.55, 2.7))), tail_amp=0.4 * bump(t, 2.6, 3.4))

# ------------------------------------------------------------------ 37 nom nom
@clip('nom_nom', 3.4, tags=('idle', 'playful', 'calm'),
      events=[fx(0.35 + 0.3 * k, 's', (-1) ** k * 12, -40, (-1) ** k * 14, 36, 0.8, 4) for k in range(8)] + [(2.6, 'sound', 'gulp')],
      desc='Happy munching: pulsing tongue, bobbing head, contented eyes, crumbs, and a big gulp.')
def _nom(t, o):
    A = env(t, 0.1, 3.4, 0.3, 0.5); ch = sm(seg(t, 0.2, 0.5)) * (1 - sm(seg(t, 2.4, 2.7)))
    return dict(tongue=(0.5 + 0.4 * abs(osc(t, 2.25))) * ch + 0.7 * bump(t, 2.6, 3.2), sy=1 + 0.025 * osc(t, 4.5) * ch + 0.06 * bump(t, 2.5, 2.9),
                head_dy=1.8 * osc(t, 2.25) * ch, blink=0.88 * A, ear_l=9 * osc(t, 2.25) * ch, ear_r=9 * osc(t, 2.25, 0.5) * ch, tail_amp=0.6 * A)

# ------------------------------------------------------------------ 38 wet dog shake
@clip('wet_dog_shake', 2.9, tags=('idle', 'playful', 'react'),
      events=burst(0.4, 'drop', 7, 135, -50, 0.7, 5, 11) + burst(0.7, 'drop', 7, 150, -50, 0.7, 5, 21) + burst(1.0, 'drop', 6, 120, -50, 0.7, 4, 31) + [(0.3, 'sound', 'whirr')],
      desc='Shakes like a wet dog: violent decaying shudder, floppy ears and flying droplets.')
def _wetdog(t, o):
    a = sm(seg(t, 0, 0.3)) * decay(max(0.0, t - 0.3), 1.3) * (1 - sm(seg(t, 2.5, 2.9)))
    return dict(rot=16 * a * osc(t, 6.5), dx=10 * a * osc(t, 6.5, 1.57), wave=7 * a, wave_k=0.05, wave_ph=2 * math.pi * 6.5 * t,
                ear_l=45 * a * osc(t, 6.5, 0.8), ear_r=45 * a * osc(t, 6.5, 0.8), tongue=0.5 * a, tongue_sway=osc(t, 6.5) * a,
                blink=0.85 * bump(t, 0.2, 1.5), tail_amp=1.2 * a, head_rot=6 * a * osc(t, 6.5, 2))

# ------------------------------------------------------------------ 39 daydream
@clip('daydream', 4.4, tags=('calm', 'idle', 'affection'),
      events=[fx(0.4, 'cloud', 84, -190, 0, -2, 3.8, 12, 0.0, 0.88), fx(0.8, 'heart', 84, -198, 0, -2, 2.6, 7, 0.0, 0.8),
              fx(2.5, 'star', 84, -198, 0, -2, 1.5, 6, 0.0, 0.7)],
      desc='Zones out into a daydream: a thought cloud with a heart, gazing up, swaying.')
def _dream(t, o):
    A = env(t, 0.1, 4.4, 0.6, 0.8)
    return dict(look_x=0.8 * A, look_y=-0.85 * A, rot=2.5 * osc(t, 0.4) * A, head_rot=5 * A, ear_l=14 * A, ear_r=14 * A,
                blink=0.3 * A + 0.7 * bump(t, 2.0, 2.4), breathe=0.02 * osc(t, 0.5) * A, tail_amp=0.4 * A, lift=3 * osc(t, 0.7) * A)

# ------------------------------------------------------------------ 40 nervous wreck
@clip('nervous_wreck', 3.4, tags=('emotion', 'react'),
      events=[fx(0.5, 'drop', 76, -112, 4, 26, 1.2, 6), fx(1.7, 'drop', -76, -108, -4, 26, 1.2, 6), fx(2.5, 'drop', 78, -114, 4, 26, 1.1, 5)],
      desc='Anxious: trembling, darting eyes, twitching ears, tucked tail and sweat drops.')
def _nerv(t, o):
    A = env(t, 0, 3.4, 0.3, 0.6)
    return dict(rot=1.6 * osc(t, 20) * A, dx=1.3 * osc(t, 23) * A, look_x=0.95 * math.tanh(6 * osc(t, 1.3)) * A, look_y=-0.2 * A,
                blink=0.9 * (max(0, osc(t, 0.9, 1.0)) ** 12) * A, ear_l=10 * osc(t, 10) * A + 6 * A, ear_r=10 * osc(t, 10, 2.0) * A + 6 * A,
                tail_amp=-0.4 * A, tail_rot=16 * A, sy=1 - 0.04 * A, head_dy=1.5 * osc(t, 17) * A)

# ------------------------------------------------------------------ 41 ta-da
@clip('ta_da', 3.4, tags=('idle', 'show', 'greet'),
      events=[(0.6, 'sound', 'tada')] + burst(0.62, 'star', 8, 105, -70, 1.1, 8, 5),
      desc='Magician reveal: crouch, spring up with ears flung wide and a star burst, then a bow.')
def _tada(t, o):
    sy = kf(t, [(0, 1), (0.35, 0.88), (0.6, 1.14, 'out_cubic'), (0.95, 1.0, 'out_elastic'), (3.4, 1.0)])
    hold = sm(seg(t, 0.55, 0.8)) * (1 - sm(seg(t, 2.5, 3.1)))
    bow = bump(t, 1.5, 2.4)
    return dict(sy=sy, sx=vol(sy), lift=kf(t, [(0, 0), (0.35, -4), (0.6, 16, 'out_cubic'), (0.95, 0, 'in_cubic'), (3.4, 0)]),
                ear_l=48 * hold, ear_r=48 * hold, head_dy=7 * bow, head_rot=-6 * bow, tail_bend=1.0 * hold, tail_amp=0.8 * hold,
                tongue=0.4 * hold, blink=0.4 * bow)

# ------------------------------------------------------------------ 42 side eye
@clip('side_eye', 2.8, tags=('emotion', 'calm', 'idle'), desc='A long, judgmental side-eye with flattened ears and a slow tail flick.')
def _sideeye(t, o):
    A = env(t, 0, 2.8, 0.35, 0.6)
    return dict(look_x=1.0 * A, look_y=0.05 * A, blink=0.38 * A, head_rot=-4 * A, ear_l=-22 * A, ear_r=-22 * A, rot=-2 * A,
                sy=1 - 0.02 * A, tail_bend=0.9 * bump(t, 1.3, 1.9), tail_amp=0.2 * A)

# ------------------------------------------------------------------ 43 dab
@clip('dab', 2.6, tags=('idle', 'show', 'playful'), events=[(0.3, 'sound', 'whoosh'), fx(0.4, 's', 90, -120, 40, -20, 0.6, 7)],
      desc='The classic dab: head ducks into one "elbow" ear, the other ear thrust out.')
def _dab(t, o):
    h = sm(seg(t, 0.15, 0.32)) * (1 - sm(seg(t, 1.7, 2.1))); ov = kf(t, [(0, 0), (0.28, 1.12, 'out_cubic'), (0.4, 1.0), (2.6, 1.0)]) if t < 2.1 else 1.0
    return dict(head_dy=6 * h * ov, head_rot=-22 * h * ov, ear_l=72 * h * ov, ear_r=-12 * h * ov, rot=3 * h, sx=1 + 0.05 * h, blink=0.5 * h,
                tail_bend=1.0 * h, lift=2 * h)

# ------------------------------------------------------------------ 44 floss
@clip('floss', 3.8, tags=('idle', 'show', 'playful'), events=[(0.5, 'sound', 'tick')],
      desc='The floss dance: hips and head swing in opposite directions while the ears swing like arms.')
def _floss(t, o):
    A = env(t, 0, 3.8, 0.4, 0.5); s = osc(t, 2.2)
    return dict(rot=-9 * s * A, body_rot=14 * s * A, head_rot=-8 * s * A, dx=9 * osc(t, 1.1) * A, ear_l=34 * max(0, s) * A - 6 * A,
                ear_r=34 * max(0, -s) * A - 6 * A, lift=2.5 * abs(s) * A, tail_bend=s * A, blink=0.3 * A, tongue=0.25 * A)

# ------------------------------------------------------------------ 45 robot dance
@clip('robot_dance', 3.6, tags=('idle', 'show', 'playful'), events=[(0.3 + 0.3125 * k, 'sound', 'tick') for k in range(10)],
      desc='Stiff robot-dance: poses snap on the beat and freeze between hits.')
def _robot(t, o):
    A = env(t, 0, 3.6, 0.2, 0.4)
    return dict(rot=steps(t, [0, 10, 0, -10, 0, 6, -6, 0], 3.2) * A, head_rot=steps(t, [-18, 18, -18, 0, 18, -18, 0, 18], 3.2) * A,
                ear_l=steps(t, [40, -10, 40, 10, -10, 40, 10, -10], 3.2) * A, ear_r=steps(t, [-10, 40, 10, 40, 40, -10, -10, 10], 3.2) * A,
                dx=steps(t, [0, 8, 0, -8, 8, 0, -8, 0], 3.2) * A, look_x=steps(t, [1, -1, 1, 0, -1, 1, 0, -1], 3.2) * A, blink=0.2 * A)

# ------------------------------------------------------------------ 46 bungee drop
@clip('bungee_drop', 3.6, tags=('idle', 'show', 'playful'), events=[(0.6, 'sound', 'boing')],
      desc='Dives downward on a bungee cord and bounces in shrinking elastic oscillations.')
def _bungee(t, o):
    if t < 0.6:
        lift = kf(t, [(0, 0), (0.25, 7, 'out_cubic'), (0.6, -48, 'in_cubic')])
    else:
        tau = t - 0.6; lift = -48 * math.exp(-1.5 * tau) * math.cos(2 * math.pi * 0.6 * tau)
    sy = 1 - 0.003 * lift
    return dict(lift=lift, sy=sy, sx=vol(sy), ear_l=-lift * 0.7, ear_r=-lift * 0.7, tongue=0.6 * bump(t, 0.2, 2.0), blink=0.6 * bump(t, 0.4, 1.4),
                tail_amp=0.8 * bump(t, 0.3, 2.5), tail_bend=-lift / 48.0, shadow=1.0)

# ------------------------------------------------------------------ 47 popcorn
_POP = [0.25, 0.5, 0.68, 0.86, 1.0, 1.15, 1.3, 1.5, 1.7, 1.95, 2.25]
@clip('popcorn', 3.0, tags=('idle', 'playful'),
      events=[(p, 'sound', 'pop') for p in _POP] + [fx(p + 0.1, 's', (hs(i) - 0.5) * 80, -100, (hs(i + 9) - 0.5) * 60, -30, 0.5, 5) for i, p in enumerate(_POP)],
      desc='Pops like popcorn in a hot pan: random hops, squashes and sparks, speeding up then calming.')
def _popcorn(t, o):
    lift = 0.0; sq = 0.0; dx = 0.0
    for i, p in enumerate(_POP):
        h = 12 + 20 * hs(i + 3); d = 0.32
        lift = max(lift, parab(t, p, p + d, h))
        sq += 0.12 * bump(t, p - 0.06, p + 0.04) + 0.1 * bump(t, p + d, p + d + 0.14)
        if p <= t <= p + d: dx += (hs(i + 5) - 0.5) * 16 * sm((t - p) / d)
    A = env(t, 0, 3.0, 0.1, 0.5)
    return dict(lift=lift, sy=1 - sq + 0.07 * min(1, lift / 14), sx=vol(1 - sq + 0.07 * min(1, lift / 14)), dx=dx * 0.4 * A,
                ear_l=22 * min(1, lift / 14), ear_r=22 * min(1, lift / 14), blink=0.5 * min(1, sq * 4), tail_amp=0.5 * A)

# ------------------------------------------------------------------ 48 confetti burst
@clip('confetti_burst', 3.0, tags=('idle', 'show', 'playful', 'greet'),
      events=[(0.7, 'sound', 'cheer'), fx(0.7, 'ring', 0, -60, 0, 0, 0.6, 24)] + burst(0.72, 'star', 8, 125, -60, 1.3, 8, 41) +
             burst(0.72, 's', 8, 90, -60, 1.1, 7, 51) + burst(0.72, 'note', 3, 70, -60, 1.5, 7, 61) + burst(0.72, 'heart', 3, 60, -60, 1.5, 7, 71),
      desc='Hops up and detonates a burst of stars, sparks, notes and hearts.')
def _confetti(t, o):
    sy = kf(t, [(0, 1), (0.3, 0.86), (0.7, 1.2, 'out_cubic'), (1.2, 1.0, 'out_elastic'), (3.0, 1.0)])
    h = sm(seg(t, 0.6, 0.85)) * (1 - sm(seg(t, 2.1, 2.7)))
    return dict(sy=sy, sx=vol(sy), lift=kf(t, [(0, 0), (0.3, -4), (0.7, 22, 'out_cubic'), (1.1, 0, 'in_cubic'), (3.0, 0)]),
                ear_l=50 * h, ear_r=50 * h, tongue=0.5 * h, blink=0.5 * h, tail_amp=1.0 * h, rot=3 * osc(t, 3.3) * h)

# ------------------------------------------------------------------ 49 tail whip
@clip('tail_whip', 2.6, tags=('idle', 'show', 'playful'),
      events=[(0.75, 'sound', 'crack'), fx(0.76, 'bang', 62, 96, 6, 4, 0.7, 9), fx(0.76, 'ring', 62, 100, 0, 0, 0.4, 14)],
      desc='Winds the tail back and cracks it like a whip, flinching on the snap.')
def _tailwhip(t, o):
    tr = kf(t, [(0, 0), (0.6, -70), (0.75, 110, 'in_cubic'), (0.88, 80, 'out_cubic'), (1.6, 0, 'out_elastic'), (2.6, 0)])
    cr = bump(t, 0.72, 1.1)
    return dict(tail_rot=tr, tail_amp=-0.5 * bump(t, 0.2, 1.8), rot=-4 * bump(t, 0.2, 0.7) + 5 * cr, ear_l=-30 * cr, ear_r=-30 * cr,
                sy=1 - 0.05 * bump(t, 0.2, 0.7) + 0.06 * cr, blink=0.9 * bump(t, 0.7, 0.95), head_dy=2 * cr)

# ------------------------------------------------------------------ 50 shrink & grow
@clip('shrink_grow', 3.6, tags=('idle', 'show', 'playful'),
      events=[fx(0.45, 'zap', -150, -40, 220, 0, 0.35, 11), (0.5, 'sound', 'charge'), fx(1.0, 'ring', 0, 0, 0, 0, 0.5, 40),
              fx(2.2, 'ring', 0, 0, 0, 0, 0.55, 30), (2.3, 'sound', 'pop')],
      desc='Zapped by a shrink ray: tiny and squeaky, then regrows with an overshoot.')
def _shrink(t, o):
    s = kf(t, [(0, 1), (0.5, 0.92), (1.0, 0.38, 'in_cubic'), (1.7, 0.38), (1.8, 0.41), (2.4, 1.18, 'out_back'), (2.95, 1.0), (3.6, 1.0)])
    sm_ = 1 - s
    return dict(sx=s, sy=s, lift=-sm_ * 150.0, ear_l=24 * bump(t, 1.0, 2.3) * osc(t, 7), ear_r=24 * bump(t, 1.0, 2.3) * osc(t, 7, 1.2),
                tail_amp=1.2 * bump(t, 1.0, 2.3), blink=0.6 * bump(t, 0.9, 1.3), tongue=0.4 * bump(t, 2.3, 3.0), shadow=s)


# =============================================================================== PLAYER
class AnimPlayer(object):
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.clip = None; self.t = 0.0; self.opts = {}
        self.out = {}; self.requests = []
        self.particles = []; self.springs = {}
        self.history = []; self.last_played = {}; self.clock = 0.0
        self._ei = 0; self.stopping = None

    # ---- selection
    def pick(self, tags=None, exclude=()):
        ok = []
        for c in CLIPS.values():
            if tags and not set(tags) & set(c.tags): continue
            if c.name in exclude: continue
            if self.history and c.name == self.history[-1]: continue
            if self.clock - self.last_played.get(c.name, -1e9) < c.cooldown: continue
            ok.append(c)
        if not ok: return None
        return self.rng.choices(ok, weights=[c.weight for c in ok])[0].name

    @property
    def active(self):
        return self.clip is not None or bool(self.springs)

    @property
    def playing(self):
        return self.clip is not None

    def play(self, name, restart=False, **opts):
        if name not in CLIPS:
            return False
        if self.clip is not None:
            if not restart:
                return False
            # Explicit restart (menu / forced clip): start cleanly instead of
            # fading the old clip out under the new one.
            self.clip = None; self.springs = {}; self.particles = []
        self.clip = CLIPS[name]; self.t = 0.0; self.opts = opts; self._ei = 0; self.stopping = None
        self.history = (self.history + [name])[-4:]; self.last_played[name] = self.clock
        return True

    def stop(self, quick=0.15):
        if self.clip is not None:
            self.stopping = quick; self._stop_t = 0.0; self._stop_from = self.t

    # ---- per frame
    def update(self, dt):
        self.clock += dt
        out = {}
        c = self.clip
        if c is not None:
            t0 = self.t
            self.t += dt
            t = self.t
            while self._ei < len(c.events) and c.events[self._ei][0] <= t:
                self._event(c.events[self._ei]); self._ei += 1
            raw = c.fn(min(t, c.dur), self.opts)
            env = sm(t / c.fade[0]) if c.fade[0] > 0 else 1.0
            env *= 1.0 - sm((t - (c.dur - c.fade[1])) / c.fade[1]) if c.fade[1] > 0 else 1.0
            if self.stopping is not None:
                self._stop_t += dt
                env *= 1.0 - sm(self._stop_t / self.stopping)
                if self._stop_t >= self.stopping: self.t = c.dur + 1
            for k, v in raw.items():
                if k in MUL: out[k] = 1.0 + (v - 1.0) * env
                elif k in SET: out[k] = v
                else: out[k] = v * env
            for k, (kk, cc) in c.lag.items():
                sp = self.springs.get(k) or self.springs.setdefault(k, Spring(kk, cc))
                out[k] = sp.step(out.get(k, 0.0), dt)
            if self.t >= c.dur:
                self.clip = None
        # springs keep settling after the clip ends (follow-through)
        for k in list(self.springs):
            sp = self.springs[k]
            if self.clip is None or k not in (self.clip.lag if self.clip else {}):
                out[k] = sp.step(0.0, dt)
                if abs(sp.x) < 0.02 and abs(sp.v) < 0.05:
                    del self.springs[k]
        # particles
        np_ = []
        for p in self.particles:
            p['age'] += dt
            if p['age'] < p['life']:
                p['x'] += p['vx'] * dt; p['y'] += p['vy'] * dt
                np_.append(p)
        self.particles = np_
        self.out = out
        return out

    def _event(self, ev):
        _, kind, data = ev
        if kind == 'fx':
            d = dict(data); d['age'] = 0.0
            self.particles.append(d)
        elif kind == 'sound':
            self.requests.append(('sound', data))
        elif kind == 'push':
            self.requests.append(('push', data['dx'], data['dy'], data['dur'], data['ease']))
        elif kind == 'pop':
            self.requests.append(('pop', data['dur'], data['ease']))
        elif kind == 'teleport':
            self.requests.append(('teleport',))
        elif kind == 'mark':
            self.requests.append(('mark',))
        elif kind == 'move':
            self.requests.append(('move', data['dx'], data['dy'], data['dur'], data.get('ease', 'lin')))

    def take_requests(self):
        r, self.requests = self.requests, []
        return r

    # ---- merge into a pose dict
    def apply(self, P, S):
        o = self.out
        P['oy'] -= o.get('lift', 0.0) * S
        P['ox'] += o.get('dx', 0.0) * S
        P['rot'] += o.get('rot', 0.0)
        P['sx'] *= o.get('sx', 1.0); P['sy'] *= o.get('sy', 1.0)
        for ch in ('head_rot', 'head_dy', 'body_rot', 'breathe', 'ear_l', 'ear_r', 'tail_rot', 'tail_bend',
                   'tail_amp', 'tail_phase', 'tongue', 'tongue_sway'):
            if ch in o: P[ch] += o[ch]
        for ch in ('look_x', 'look_y'):
            if ch in o: P[ch] = clamp(P[ch] + o[ch], -1.6, 1.6)
        if 'blink' in o: P['blink'] = max(P['blink'], clamp(o['blink'], 0.0, 1.0))
        P['shadow'] *= o.get('shadow', 1.0)
        P['wave'] = o.get('wave', 0.0); P['wave_k'] = o.get('wave_k', 0.05); P['wave_ph'] = o.get('wave_ph', 0.0)
        if self.particles:
            fxl = list(P['fx'])
            for p in self.particles:
                u = p['age'] / p['life']
                hd = p.get('hold', 0.5)
                a = min(1.0, u * 8.0) * (1.0 - sm((u - hd) / max(0.01, 1.0 - hd)))
                size = p['size'] * ((1.0 + p.get('grow', 1.6) * u) if p['kind'] == 'ring' else (1.0 + 0.25 * u))
                fxl.append((p['kind'], p['x'], p['y'], size, a, p.get('rot', 0.0)))
            P['fx'] = fxl
        return P
