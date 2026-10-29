# Chill Pet — local LLM reaction layer

## Start

Double-click `run_chill_pet.bat`, or run `python chill_pet.py` in this folder.

The pet works fully without the optional model: click, double-click, drag, reactions, animation, effects, sounds, blush, and saved disposition are all available immediately.

## Included sound pack

`sounds/` contains safe, self-contained WAV copies made from the sound files you supplied. The originals in Downloads are never changed.

- `click-meow.wav` — pet clicks and short thought/speech replies.
- `sleep-purr.wav` — entering sleep.
- `wobble-01.wav` through `wobble-06.wav` — only carrying/dragging, landing, and throwing the pet around.
- `minecraft-01.wav` through `minecraft-26.wav` — spins, celebrations, startled/rough reactions, and other non-carry animation beats.

The Wobble and Minecraft collections are shuffle bags: a clip cannot repeat until every clip in that collection has played. Tiny fades are applied only to the beginning/end of the exported copies to avoid audible clicks; the sound inside every clip is preserved. One light clip is selected per reaction frame, so a talking meow will not interrupt a movement effect.

Sound is enabled by default for a new profile. Use the right-click menu’s **Soft reaction sounds** switch to mute/unmute it, or **Sound volume** to select 0%, 20%, 40%, 65%, 85%, or 100%. Use **Test sound now** if you need to verify Windows audio. Volume-adjusted temporary WAVs are cached under Windows Temp because Windows cannot asynchronously play in-memory WAVs; it never modifies the packaged clips or your original recordings.

## Enable local thinking

Right-click the pet and select **Let him think** after completing the one-time manual setup below. The pet never installs packages or downloads a model itself. Nothing is sent to a server during reactions.

The preferred brain is **Qwen3.5-0.8B Q4_K_M** (about 503 MB): a newer, more capable compact model that stays below 1 GB. The older Qwen3-0.6B and Qwen2.5-0.5B files remain fallbacks if Qwen3.5 is not present.

The model needs an NVIDIA GPU with working CUDA support. If setup cannot obtain GPU offload or VRAM, the pet silently continues in deterministic mode.

Run these commands yourself in PowerShell, from this folder:

```powershell
python -m pip install --upgrade llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
python -m pip install --upgrade nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
New-Item -ItemType Directory -Force "$HOME\.chill_pet\models"
curl.exe -L -o "$HOME\.chill_pet\models\Qwen3.5-0.8B.Q4_K_M.gguf" "https://huggingface.co/mradermacher/Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B.Q4_K_M.gguf"
```

Restart the pet and select **Let him think**. The Qwen3.5 model file is about 503 MB. If CUDA 12.4 is not compatible with your installed NVIDIA driver/toolkit, change `cu124` to the matching available wheel index: `cu121`, `cu122`, or `cu123`.

## Options

- `python chill_pet.py --think` — enable local thinking at launch.
- `python chill_pet.py --no-think` — launch without it.
- `python chill_pet.py --model C:\path\to\model.gguf` — use a compatible local model.
- `python chill_pet.py --llm-debug` — print local model timing diagnostics.

## Animation pack (66 clips)

`pet_anims.py` adds 66 additive performance clips across idle, playful, show, emotion, affection, sleepy, music, greet, and travel categories. They layer over breathing, gaze, blinking, and existing reactions instead of replacing the pose, so the pet keeps living underneath.

Right-click → **Animations** to play any clip, or switch the whole pack off. In Chill mode the pack gets first refusal on about 55% of idle fun slots; its own history and per-clip cooldowns prevent immediate repeats. Reactions, naps, perches, walks, icon carries, and cursor games keep priority.

Five clips move the real window: **rocket**, **teleport**, **zoomies**, **loop_the_loop**, and **yoyo**. Scripted travel is off by default for the automatic idle picker; enable **Animations → Travel clips** or choose one explicitly from the menu. Clicking an ordinary clip fades it out; clicking a travel clip lets it land first, and dragging cancels both the clip and its scripted path so the pet can never be stranded mid-air. Travel targets are clamped to the pet's normal screen bounds.

While a pack clip is playing, the borderless window temporarily gains 92 design pixels of transparent headroom on every side—enough for `jack_in_box`'s pop, the rocket exhaust, and the teleport stretch—then returns to its compact size. The character stays at the same screen position, and the extra transparent area is excluded from the pet's walk bounds so he does not teleport or refuse to approach a screen edge.

With **Reduced motion**, low energy, or a slow frame, flashy show/playful clips are skipped in favour of calm/idle ones; travel clips wait for an explicit choice. Video, game, and fullscreen foregrounds pause automatic clips; travel clips also skip work apps. **Still** mode never starts travel clips on its own. Clip sound cues reuse the existing sound bank, cooldown, volume, and mute settings. If `pet_anims.py` is missing or raises an error, the pet runs exactly as before and simply hides the Animations menu / disables the layer for that session.

The renderer also gained all pack effect glyphs (`heart`, `bulb`, `ring`, `bang`, `quest`, `note`, `drop`, `star`, `zap`, `cloud`) with their white halos, negative `sx` mirroring for `coin_flip`, and the optional sine `wave` warp used by the jelly clips. Host reaction glyphs keep their original short-code art.

## Included visual checks

`animation-check/` contains off-screen Qt captures of the actual renderer for hop, spin, nuzzle/blush, and sleepy-settle reactions.

## Roadmap features now included

- Daily rhythm from the local clock: soft morning stretches, afternoon energy, and late-night sleepy behavior.
- Manual optional weather ambience: choose **Rainy day** from **Personality & accessibility** for quiet window-watching behavior. It has no network or location access.
- Quiet scrapbook achievements persisted in the local state: gentle-pet milestone, night owl, long perch, and deep nap.
- Export/import of a small personality profile JSON: name/settings, disposition, comfort, perches, and scrapbook only.
- Accessibility controls: reduced motion, energy level, sound volume, silent mode, low frame-rate mode, fullscreen visibility, and app reaction control.
- Opt-in local integration adapters under `integrations/` for Brave/YouTube and VS Code. Their setup and narrow privacy boundary are documented in [integrations/README.md](integrations/README.md).

## Desktop icon carry

Under **Advanced local actions**, enable **Let him rearrange desktop icons**, then use **Move one desktop icon now**. This changes only the icon's visual position in Windows Explorer—never the file itself, its name, or its folder. The selected icon travels through a 1.55-second curved path in real time; the pet performs ears-first pickup, a tail-assisted carry, and a visible release/landing. Turn off Windows **View → Auto arrange icons** if Explorer immediately puts the icon back.

The carry now uses a 2.2-second three-stage path: the pet approaches the source icon first, grips it with the ears, travels with it along the same smooth curve, then releases and lands. This needs the normal Explorer desktop; it cannot be demonstrated in an isolated/offscreen test environment.

**Icon carry control** has three levels: **Manual only** (you start every move), **Assisted: ask me first** (local thinking may ask through a bubble and you use **Approve pet carry request** within 18 seconds), and **Autonomous with cooldown** (local thinking may initiate a carry only after its 95–180 second cooldown). All modes still require **Let him rearrange desktop icons** to be enabled first.

If cursor curiosity is disabled, any current voluntary cursor visit stops immediately. Switching from **Follow my cursor** to **Chill** also stops following immediately while preserving the separate curiosity preference.

The creative idle catalogue also includes bow, tail chase, peekaboo, moonwalk, balance, short sprint, and investigate sketches in addition to the existing look, wiggle, lick, hop, and spin. Immediate repeats are prevented. The sprint has its own crouch → curved dash → overshoot/brake → landing recovery, while investigate uses ear radar, a low lean, head tracking, tail cue, and a curious pause.

## Reaction variety and comic speech bubbles

Every reaction family now has at least **10 full choreography scripts** (10–11 in the current catalog). A script changes the visible motion sequence—anticipation, hop count, lean, spin, tail action, ear pose, effect glyphs, and recovery—not merely timing. The next reaction excludes the immediately previous behavior when another valid behavior is available, and the same behavior also avoids its most recent variant.

Speech balloons are drawn by the application rather than styled as a plain rectangle. Their shape follows the selected response: oval for curiosity, cloud for affection/sleepiness, comic burst for startled/annoyed, and a rounded balloon for playful/other replies. Each has an ink outline, pointed tail, small face/status mark, and comic-dot shadow.

## Distinct character acts

Beyond the reaction catalog, Chill has twelve staged set-pieces with recognizable silhouettes and multi-beat acting: **stalk, pounce, sneeze, stargaze, proud pose, hide-and-peek, yoga, dizzy recovery, bubble dance, butterfly chase, tail knot, and rain shiver**. These are continuous pose performances—not small timing variants—and remain subject to the global no-immediate-repeat rule. A real renderer montage is saved at `animation-check/character-act-poses.png`.
