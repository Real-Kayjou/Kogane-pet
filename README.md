# Chill Pet 🐾

A little desktop pet with a personality of its own.

Chill Pet lives on your desktop, reacts to your interactions, wanders around, makes little sounds, and slowly develops its own habits. Sometimes playful, sometimes sleepy, sometimes just watching.

It's built to feel alive without getting in the way.

## What can it do?

- **Play and react** — playful movements, expressive animations, comic-style speech bubbles, and sound effects.
- **Develop a personality** — affection, comfort, curiosity, energy, and other traits shape its behavior.
- **Follow its own rhythm** — morning stretches, afternoon energy, late-night naps, and small achievements.
- **Play with you** — cursor games, different interactions, and plenty of little character moments.
- **Think locally** — an optional local language model adds another layer to its reactions. The pet still works without it.
- **Make itself at home** — remember favorite perches and keep a small scrapbook of moments.

## Getting started

1. Download or clone this repository.
2. Open the project folder.
3. Double-click `run_chill_pet.bat`, or run:

   ```powershell
   python chill_pet.py
   ```

Python and the project's required dependencies must be installed. The basic pet does not require a language model.

## Optional local AI

Chill Pet can use a local Qwen model through `llama.cpp` for additional reactions. This requires manual setup and a compatible NVIDIA GPU with working CUDA support.

See the local-thinking setup in this README's history or project documentation for the current installation commands and model details.

## Privacy comes first

The core pet is designed to run locally. Its normal app awareness uses broad foreground-app categories and window information rather than recording your screen or keystrokes.

Optional Brave/YouTube and VS Code integrations require separate setup and opt-in. They use limited local signals rather than unrestricted access to your browser or project.

Chill Pet is meant to keep you company, not take control of your computer.

## A little more

There's more going on beneath the animations: sound controls, accessibility options, different animation styles, saved personality settings, and more.

The [roadmap](ROADMAP.md) has a closer look at the ideas behind the project and its design.

---

Made with a lot of little details, and hopefully a little character.
