# Optional local companion integrations

These adapters communicate only with the running Chill Pet at `127.0.0.1:8765`. They do not upload anything and send no page text, browser history, URLs, account information, source code, filenames, keystrokes, or clicks.

## Brave / YouTube

1. In the pet’s right-click menu, enable **Brave / VS Code companion bridge**.
2. Open `brave://extensions`, enable **Developer mode**, select **Load unpacked**, and select `integrations/brave-youtube`.

The adapter sends only play/pause/fullscreen/music state from YouTube. The pet's explicit **Like current YouTube video** menu item can issue one visible Like request; it never subscribes, clicks ads, or acts autonomously.

## VS Code

1. Enable the pet’s companion bridge.
2. Install the folder as a local development extension, then set `chillPet.enabled` to true in VS Code settings.

To package it yourself, install the packager and run this from `integrations/vscode-chill-pet`:

```powershell
npm install --global @vscode/vsce
vsce package
code --install-extension chill-pet-companion-1.0.0.vsix
```

The extension sends only: save event, active language ID, diagnostics becoming clean, or diagnostics containing errors. If you separately enable `chillPet.shareCurrentDocument`, it sends the active document (capped at 12 KB) only to the local pet bridge at `127.0.0.1`; it is RAM-only in the pet and is not persisted, uploaded, or sent to the language model by default.
