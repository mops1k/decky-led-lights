# Led Lights ✨

RGB LED control for handheld gaming PCs, packaged as a [Decky Loader](https://github.com/decky-loader) plugin.

English · [Русский](README.ru.md)

<p align="center">
  <a href="https://github.com/mops1k/decky-led-lights/actions/workflows/ci.yml"><img src="https://github.com/mops1k/decky-led-lights/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/mops1k/decky-led-lights/releases/latest"><img src="https://img.shields.io/github/v/release/mops1k/decky-led-lights?label=latest&color=blue" alt="Latest release"></a>
</p>

Led Lights is a fork of [Colores](https://github.com/Hooandee/decky-colores) by
[Hooandee](https://github.com/Hooandee). It detects your handheld at boot and only
offers what the machine can really do: no device list, no config files. Open it from
the Steam Quick Access Menu and pick a colour.

## What this fork adds

- **Edge ambilight.** The upstream layout feeds each stick ring from the half of the
  screen around that stick. This fork adds a second layout, **Screen edges**: every
  LED takes the colour of the nearest screen border, like a TV ambilight. The four
  left-stick zones read the left edge, the four right-stick zones the right edge, and
  the ABXY zone reads the bottom edge. Both layouts are selectable in the panel.
- **Ambilight in Desktop Mode.** Upstream only captures in Game Mode through PipeWire
  from gamescope. Led Lights keeps that path and adds a desktop path: the active
  screen is captured through KWin's `ScreenShot2` D-Bus API, with the same smoothing,
  vividness and capture-rate controls. No portal dialog, no extra permissions.
- **Languages.** English is the default language and a complete Russian translation
  is included. The self-updater is disabled: the fork has no release feed of its own.
- **Sound that flashes instead of strobing.** With sound reaction enabled, the lights
  stay at a configurable base brightness (80 % by default) and flash to full on
  detected beats, so the screen colours stay visible in silence. The old build dimmed
  the whole case to 25 % between sounds and repainted up to 30 times a second.
- **Quieter, cheaper capture.** A deadband ignores single-level colour noise so static
  screens do not shimmer, a static screen drops the capture rate to 3 fps, and the
  capture helper is recycled if it stops producing frames.
- **CI and packaging.** GitHub Actions run the backend and frontend test suites, build
  the bundle and attach a ready-to-install ZIP; tagging `v*` publishes a release.

## Supported devices

| Brand | Models | Details |
| --- | --- | --- |
| MSI | Claw, Claw A8, Claw 8 AI+ | 9 zones: 4 around the right stick, 4 around the left stick, 1 on ABXY |
| ASUS ROG | Ally, Ally X, Xbox Ally, Xbox Ally X | 4 RGB zones on the joystick rings |
| Lenovo Legion | Go, Go 2, Go S | Per-controller colour, reconnect button (see below) |
| AYN | Thor, Odin 3 (Linux, Armada OS) | 8 zones on the joystick rings |

The edge layout and the desktop capture path were developed and verified on a
**MSI Claw A8 (BZ2EM)**, 9 zones. On devices with a single global LED the plugin
falls back to one averaged colour.

Any other handheld: Led Lights still tries to drive whatever LEDs the system exposes
and marks those features experimental, so nothing hangs when there is nothing to
control.

## What you can do

- **Solid colour** with hue and saturation controls.
- **Brightness** on the devices that allow it.
- **Gradients** with a per-zone editor, presets (Sunset, Ocean, Aurora, Lava, Galaxy
  and more), an auto palette, a "Surprise me" button and saved favourites.
- **Effects**: breathing, rainbow, wave, cycle and spiral, optionally running on your
  own gradient.
- **Ambilight** that follows the screen, with vividness, smoothing, capture rate, an
  optional beat flash and a base-brightness control for it.
- **Sensors, clock and audio modes**: battery level, temperature, performance load,
  a time-of-day tint and a VU meter.
- **Languages**: English (default), Spanish, Russian, Italian, German, Brazilian
  Portuguese and French, switchable in Settings.

## Ambilight capture

| Session | Source | Helper |
| --- | --- | --- |
| Game Mode | gamescope PipeWire node (`pw-dump` / `wpctl`) | `py_modules/pw_capture.py` (falls back to GStreamer) |
| Desktop Mode (KDE) | KWin `org.kde.KWin.ScreenShot2` → `CaptureActiveScreen` | `py_modules/kwin_capture.py` |

The plugin picks the source automatically: a running gamescope compositor means Game
Mode, otherwise it captures the desktop. Frames are downscaled to 32×18 on the host
Python (numpy when present) and smoothed in the plugin process. The desktop path is
capped at 15 fps and measured at roughly 8 % of one CPU core on the Claw A8.

## Installation

1. Download `LedLights-<version>.zip` from the [latest release](https://github.com/mops1k/decky-led-lights/releases/latest).
2. In Decky, enable Developer mode and use **Install Plugin from ZIP**.

The plugin needs root to write the LEDs; Decky asks for it during installation.

> **Do not install Led Lights next to Colores.** Both plugins talk to the same HID
> zones and will fight over them. Uninstall or disable the other one first.

## Using it

1. Open the Quick Access Menu and select **Led Lights**.
2. Choose the **Ambilight** mode tab.
3. Pick the layout: **Sticks** (upstream behaviour) or **Screen edges** (TV-style).
4. With the edge layout, tune the border width (**Edge size**), flip the ring order
   (**Reverse ring order**) and choose where the ABXY zone reads from (**Bottom**,
   **Top** or **Center**).
5. Press **Test zones** to light every zone in turn: 0-3 is the right stick ring,
   4-7 the left ring and 8 the ABXY buttons. If the order looks mirrored on your
   unit, toggle **Reverse ring order**.
6. Tune vividness, smoothing and capture rate. LED colour follows the nearest screen
   border, so a red wall on the left lights the left stick ring red.

## Development

```bash
pnpm install && pnpm build     # bundle the frontend into dist/index.js
pnpm typecheck                 # tsc --noEmit
pnpm test:fe                   # vitest
python -m pytest               # backend tests
python3 scripts/package.py     # build packages/LedLights-<version>.zip
```

Install a locally built ZIP with Decky's Developer menu, or from a shell as root:

```bash
pkexec bash scripts/install.sh packages/LedLights-<version>.zip
systemctl restart plugin_loader
```

## CI

`.github/workflows/ci.yml` runs on every push and pull request:

- **backend**: `ruff check` and `pytest` on Python 3.11
- **frontend**: `pnpm typecheck`, i18n content tests, `vitest` and the production build,
  on Node 20 and 22 with pnpm cache
- **package**: builds the ZIP, verifies its layout with
  `scripts/verify_package.py` (bundle present, required modules, one top-level
  folder, archive name matches the version) and uploads it as a workflow artifact

`.github/workflows/release.yml` runs on `v*` tags: stamps the version from the tag
(`scripts/stamp_version.py`), builds and verifies the ZIP, generates release notes and
uploads the asset.

## Known limitations

- Desktop capture uses KWin (Plasma). GNOME/Sway sessions have no capture path yet.
- Game Mode capture inherits the upstream PipeWire path; it has not been re-verified
  on this fork's build yet.
- Lenovo Legion Go: controllers can lose the colour channel after suspend (use
  **Reconnect controllers**) and only one colour per controller is possible in
  firmware; the spiral effect is the firmware's own.
- Unlisted devices run in experimental mode until someone calibrates them.

## Credits

- [Hooandee/decky-colores](https://github.com/Hooandee/decky-colores) — the upstream
  plugin this project is forked from.
- [honjow/HueSync](https://github.com/honjow/HueSync) — the per-device lighting engine
  (BSD 3-Clause; its notice is kept in [NOTICE](NOTICE)).
- [Decky plugin template](https://github.com/SteamDeckHomebrew/decky-plugin-template)
  by Steam Deck Homebrew.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). Any fork or distributed build must publish
its source under the GPL as well. Portions adapted from HueSync keep their BSD
3-Clause notice (GPL-compatible); see [NOTICE](NOTICE).
