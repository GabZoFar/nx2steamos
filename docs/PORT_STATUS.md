# Port status

Every game nx2steamos can port, and whether it plays on a SteamOS device. This page is the only place either is kept; a profile's `steamos.tsv` (`status`, `tested`) says the same in short.

## Devices

| Device | SoC, GPU | SteamOS | Status |
|---|---|---|---|
| Lenovo Legion Y700 Gen 4 (TB322FC) | Snapdragon 8 Elite, Adreno 830 | SteamOS-ARM-Port (Mesa 26.2 + the a830 patch) | not tried yet; nx2android's Android games were brought up on this tablet |
| KONKR Pocket FIT, AYANEO Pocket S2 | Snapdragon 8 Gen 3, Adreno 750 | SteamOS-ARM-Port (Valve's Mesa) | not tried yet |
| AYN Odin 2 / 3, Thor, Retroid Pocket 6 … | Snapdragon 8 Gen 2 / 8 Elite | SteamOS-ARM-Port | not tried yet |
| Steam Frame | Snapdragon 8 Gen 3, Adreno 750 | Valve's SteamOS (VR session) | not tried yet |
| Steam Deck (LCD, OLED) | AMD Van Gogh / Sephiroth (Zen 2, RDNA 2) | SteamOS 3 | the recompiler engine (`recomp/`, [RECOMP.md](RECOMP.md)): built and tested in an x86-64 container, its game folders made by the porter (`--engine recomp`), Tears of the Kingdom has run on one through nx2steamos (2026-10-30, SteamOS 3.9.5, RADV); Savage Wind Recomp itself has played *Breath of the Wild* 1.9.0 on one (SteamOS 3.8, RADV) |

A first run on an ARM64 device: `python -m porter device` (what it is), the probes (`probes/README.md`), then one of the games below.

## Games on the Steam Deck

| Game | Title ID | Steam Deck (`recomp/`) | Through nx2steamos (`deck.tsv`) |
|---|---|---|---|
| The Legend of Zelda: Breath of the Wild 1.8.2, 1.9.0 | `01007EF00011E000` | Savage Wind's game: the opening and the Great Plateau, played by its testers, about 40 fps on a Deck with the 60 fps cap | untested: the porter makes its folder (`--engine recomp`) |
| The Legend of Zelda: Tears of the Kingdom 1.4.3 | `0100F2C0115B6000` | runs on a Steam Deck (2026-10-30): menus, saves, the opening and its cutscenes lit, at 30 fps (60 where its frames allow) with sound; some surfaces still wrong ([RECOMP.md](RECOMP.md), "Games") | experimental: `deck.tsv` selects the site-free runtime path; custom AppImage required |

## Games on ARM64 SteamOS

The games nx2android brought up on Android replay NVN with the same Vulkan backend, so they are offered as **untested** until someone plays them on SteamOS. None has been played on SteamOS yet.

| Game | Title ID | Android (nx2android) | SteamOS |
|---|---|---|---|
| 13 Sentinels: Aegis Rim | `01003FC01670C000` | playable | untested |
| Graveyard Keeper 2 | `0100005027A18000` | playable | untested |
| Hades II | `0100A00019DE0000` | playable | untested |
| Mario vs. Donkey Kong | `0100B99019412000` | playable | untested |
| NEO: The World Ends with You | `010043B013C5C000` | playable | untested |
| Tales of Eternia Remastered | `010006E0230AC000` | playable | untested |
| Unicorn Overlord | `010069401ADB8000` (Asia `010054B01AD92000`) | boots | untested |
| Unpacking | `0100701015846000` | playable | untested |
| Viractal | `0100FD501F95A000` | playable | untested |
| Xenoblade Chronicles 3 | `010074F013262000` | work in progress | untested |

The other 49 profiles (nxnative's iOS games) can be tried with `--experimental`; games that render with Vulkan or OpenGL instead of NVN, or need a step that boots the game in the Mac host, are refused for now (as on Android).

## Reporting a game

With the game, its version, the device and its SteamOS image: the log (`~/.local/state/nx2steamos/<title ID>/log.txt`, and `log.prev.txt` for the run before), what was played, and the frame rate (the pause menu's counter, or the Steam performance overlay). A game that plays gets its `steamos.tsv` (`status playable`, `tested <version> <device>`) and its row here.
