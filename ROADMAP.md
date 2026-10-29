# Chill Pet — Companion Roadmap

## Purpose

Chill Pet is a small, wobbly desktop companion: expressive enough to feel alive, but quiet, safe, and non-disruptive around real work. It reacts immediately through deterministic animation, while an optional local language model chooses safe, short intentions and speech from a constrained set of choices.

The core rule is simple: the pet decorates and responds to the computer environment; it does not take control of it.

## Design principles

- **Cute character first.** The pet has weight, curiosity, comfort, shyness, affection, and boundaries.
- **Never interfere.** It avoids essential controls, does not resize windows, change files, type, click, move desktop icons, or send actions on the user's behalf.
- **Graceful fallback.** Every feature works sensibly when the model, a companion integration, audio, network access, or GPU is unavailable.
- **Privacy by default.** The normal app watcher uses only coarse foreground-app category, window geometry, and optional symbolic perches. It does not record screen contents, keystrokes, browser history, code, titles, or private data.
- **Opt-in deeper integrations.** Website/video state and VS Code events require separately enabled local companion integrations with narrow permissions.
- **No notification clutter.** Most rewards and memories are quiet visual moments or scrapbook entries, not pop-ups.

## Existing foundation

The current build already includes:

- Persisted disposition, affection, comfort, wariness, symbolic places, and daily decay.
- A deterministic reaction catalog with visual glyph effects, cheek blush, procedural fallback sounds, and overlay animation channels.
- An optional local Qwen/llama.cpp thought layer with GPU support when installed.
- Strict action/intent/line grammars so generated speech is selected from safe curated choices rather than invented free text.
- A speech bubble that follows the pet, remains visible longer, fades gently, and reports thinking/fallback states.
- Cursor following plus occasional consent-free, non-clicking cursor curiosity in Chill mode.
- App-category moods, fullscreen visibility settings, remembered screen-corner perches, and basic interaction tracking.

## Animation direction

The pet must never feel like it is replaying one loop. Reactions select from multiple variants, directions, intensity levels, and timing offsets, with cooldowns that prevent repetition.

### Animation principles

- **Anticipation:** a crouch, tilt, glance, or tail gather before a hop, spin, nuzzle, or dash.
- **Squash and stretch:** restrained compression on landing and gentle lift on takeoff; cute, never rubbery.
- **Ease in and ease out:** movements accelerate and settle naturally instead of starting or stopping abruptly.
- **Follow-through:** ears, tail, cheeks, and body settle at different speeds after the main motion.
- **Overlapping action:** blinking, gaze, breathing, tail curl, and head movement do not all begin/end together.
- **Secondary motion:** small balance corrections, ear flicks, breathing, cheek reactions, and tail sways continue around the main action.
- **Wobbly hand-made timing:** subtly uneven offsets and spring motion retain the playful, hand-animated feel.

### Reaction families

Each family should have at least two to four variants, left/right choices where applicable, and an idle return.

| Family | Examples |
| --- | --- |
| Curious | perk, lean, peek, small double-hop, gaze-follow |
| Happy | wiggle, bouncy hop, tiny dance, excited spin |
| Affectionate | nuzzle, blush, purr settle, heart effect |
| Startled/annoyed | flinch, recoil, tail puff, brief wary glance |
| Sleepy | yawn, droop, curl, slow blink, sleepy perch |
| Play | cursor chase, catch-me dash, follow-the-dot, rhythm bounce |
| Rare | alternate dance, surprise sparkle, birthday-style flourish |

The pet must respect screen edges, taskbar, window controls, fullscreen settings, and cursor space. It may visually perch near UI elements, but must not cover essential controls for long or manipulate them.

## Personality, memory, and daily rhythm

### Live disposition

Core values: affection, comfort, energy, mischief, shyness, curiosity, wariness, and mood. These influence tail curl, gaze, blink timing, ear angle, blush, bubble tone, sound pitch, and animation choice.

### Compact memory card

Persist only compact, user-meaningful state:

- Current broad app category (desktop, work, browser, video, music, game, chat).
- Cursor relationship (ignored, watching, invited, playing).
- Recent safe interactions and action cooldowns.
- Favorite symbolic perches (for example, `lower left corner`, not raw application data).
- Comfort/relationship progression and quiet achievements.

The model receives this compact card, never a raw screenshot, web page, private title, keystrokes, or full code file unless a future setting explicitly grants a narrow integration permission.

### Rhythm and ambience

- Morning: wake/stretch, gentle curiosity.
- Afternoon: playful peak and more energetic variants.
- Evening: calmer perches and relaxed interaction.
- Late night: sleepy palette, quieter sounds, resting behavior.
- Optional weather ambience: locally selected weather/location only when enabled; rain can trigger window-watching and curl-up behavior. No weather/location is stored by default.

### Achievements and profile

Keep a small scrapbook JSON: first long perch, 100 gentle pets, night owl, rare dance, favorite window category. Provide import/export as one portable pet-profile JSON. Achievements are quiet and do not clutter the desktop.

## App and website rituals

### Shared behavior

For unknown apps, infer only a broad category and use unobtrusive rituals: work buddy, media watcher, game caution, browser explorer, chat companion, or desktop wanderer. User-defined no-go apps/areas always win.

### Brave and YouTube — opt-in companion integration

Without the integration, the pet can only use coarse window category/title hints and remain visually safe. With a user-enabled local Brave companion:

- Allow-list YouTube first, then enable optional generic-browser cues later.
- Identify safe visual anchor regions such as browser chrome, title area, or (when not fullscreen) the vicinity of Like/Subscribe controls.
- Perch **near** those elements; never click Like, Subscribe, ads, tabs, links, forms, or media controls.
- React visually to play/pause, long viewing, music/video type, and tab changes when those signals are granted.
- Head-bob gently for music; settle down for long video; stay away from the player and avoid fullscreen video when configured.
- Do not scrape browsing history, page text, account identity, messages, or form data.

### VS Code — opt-in companion integration

Default behavior is a quiet work-buddy posture based only on the foreground app category. A separately enabled VS Code companion can provide narrow events:

- File save, active language, run/build completion, and diagnostic severity.
- Subtle celebration for a successful build/run, no intrusive banner.
- Thinking/settling behavior during focused coding.
- Optional current-file context is a separate setting. It is off by default and must clearly state what is shared; no project-wide scanning, uploading, or retention.

### Desktop and windows

- Perch on safe screen edges, taskbar-adjacent areas, window title-bar regions, or remembered corners.
- Travel *between places near* desktop icons rather than moving, opening, renaming, or rearranging the actual icons/files.
- Return to familiar safe perches occasionally.
- Learn user-declared no-cover zones and favorite locations.

## Interaction and micro-games

- Direction-sensitive pets and head rubs.
- Belly poke, gentle toss, rough toss, holding/bringing the pet near the cursor, and recovery after unwanted roughness.
- Cursor catch-me, follow-the-dot, and a short click-rhythm game.
- A clear mode/cooldown so games never begin during focused work without invitation.
- Relationship behavior: gentle interactions raise trust; repeated rough handling produces temporary wariness that recovers over time.

## Speech, thought mode, and conversation

### Reliable thought output

The language model chooses only from an exact, grammar-constrained list of valid actions, safe intentions, and curated short lines. It does not freely generate speech. This prevents hallucinated/random phrases.

Improve variation by tracking recently used:

- Lines, actions, intentions, and animation variants.
- Current app category, time-of-day, interaction style, comfort, and energy.
- Per-action line cooldowns and weighted rotation.

### Bubble behavior

- A real rounded speech bubble with a face/tail motif, soft outline, status icon, and gentle fade.
- Bubble follows the pet anchor, avoids screen edges, and remains visible long enough to read.
- Status states: thinking, loading local model, reply, fallback, unavailable, muted.
- Conversation mode offers a few safe reply choices instead of unrestricted chat.

## Sound plan

Use the user's supplied clips where available, with procedural synthesis as a no-dependency fallback.

- Sound groups: purr, soft chirp, sleepy breath, sparkle, happy burst, startled squeak, soft rustle.
- Master volume plus per-category volume, cooldowns, pitch variation, and a global silent mode.
- Automatically reduce/disable sound around fullscreen video, games, or a user-selected quiet mode.
- Match sound envelope/pitch to mood: warm/low when comfortable, light/high when playful, short/soft when shy.
- Keep a small documented sound-pack folder. Only use assets the user owns or has permission to use.

## Customization and accessibility

- Name, favorite color, voice/sound style, sound volume, energy, shyness, sleep schedule.
- Reduced motion, high contrast, silent mode, low CPU mode, no-fullscreen-overlay, app-specific hide rules.
- A lightweight customization panel instead of a crowded dashboard.
- Every deeper integration must be individually visible, enabled, disabled, and revocable in settings.

## Technical architecture

1. **Deterministic core:** physics/springs, animation overlays, app-category rules, input handling, audio, persistence, and fallbacks.
2. **Reaction catalog:** safe action variants, effect presets, sound mappings, and cooldown metadata.
3. **Thought layer:** local model selects from constrained action/intent/line choices only; strict timeout, cancellation, and fallback.
4. **Integration adapters:** separate, optional Brave/YouTube and VS Code adapters that expose only the specific signals granted.
5. **Settings/privacy boundary:** permissions and feature flags are checked before every adapter read.
6. **Tests:** deterministic behavior tests, grammar parsing, state migration, no-model fallback, integration-mock tests, and real visual animation checks.

## Implementation phases

### Phase 1 — polish the current core

Finish reaction variants, rounded face-like bubble, non-repeating curated thought choices, sound-pack loader, animation cooldowns, and full accessibility/settings surface.

### Phase 2 — rhythm, games, scrapbook, and custom personality

Add daily rhythm, weather toggle, micro-games, interaction nuance, quiet achievements, export/import, and user-defined perches/no-go zones.

### Phase 3 — browser companion

Build an opt-in local Brave/YouTube adapter. Start with visual anchors and video state; confirm no accidental pointer/click control and validate fullscreen behavior.

### Phase 4 — VS Code companion

Build an opt-in VS Code extension/adapter for save/build/language events. Keep file-content access disabled unless separately requested and transparently enabled.

### Phase 5 — verification and packaging

Run real animation renders and live overlay checks, test fallback paths, verify settings persistence/migration, package the project in one folder, and document any user-run installation commands separately.

## Explicit non-goals unless separately requested

- Automatically clicking Like/Subscribe or any web/app control.
- Reading browser pages, chats, passwords, or forms.
- Moving, deleting, renaming, opening, or uploading the user's files/icons.
- Recording screens, keystrokes, private code, or browsing history.
- Automatically downloading/installing dependencies or models.

## Decisions to make before implementation

1. Which supplied sound files map to which actions, and where are they located?
2. Should VS Code code-context access stay fully off, or be offered as an explicit per-project opt-in later?
3. Should YouTube recognition be limited to YouTube, or eventually include a short allow-list such as Spotify/Twitch?
4. What name, favorite color, and sleep hours should the initial pet profile use?

## Delivery status — current build

Implemented in the standalone pet: deterministic reaction core, constrained local-thinking layer, ten-or-more animation takes per action, comic response balloons, sound banks/volume controls, daily rhythm, manual no-network weather ambience, scrapbook/profile export-import, custom name/energy/reduced-motion controls, app-category rituals, cursor relationship, remembered perches, full-screen controls, and accessibility switches.

Implemented as separately enabled local adapters: YouTube/Brave playback-state companion and VS Code save/diagnostic companion under `integrations/`. They remain disabled by default and require the manual setup in `integrations/README.md`; they transmit only their declared minimal local cues over loopback.

Intentionally not included: automatic clicking/liking/subscribing, file/icon modification, page/source-code scraping, keylogging, recording, automatic downloads/installation, or external weather/location requests. Those would violate the stated non-interference and privacy boundary rather than completing the project safely.

