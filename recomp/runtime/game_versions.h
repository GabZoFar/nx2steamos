// The game versions Savage Wind knows, by the build id of their main module (its first 8 bytes as hex, as
// tools/sw_probe.py prints it). The configuration screen sets up the supported ones; a known one that isn't supported
// yet is refused with its own message. tools/game_sites.py VERSIONS is the same list for the code generator
// (tools/test_game_sites.py checks they match); docs/SAVAGE-WIND.md, "Versiones del juego", has the steps for a
// new version. X(version, build id, supported, RomFS files, RomFS marker, game sites)
// RomFS files: how many its RomFS has with the update applied over the base game (0: not counted). Before copying, the
// setup checks its marker and at least 95% of these files. System/Version.txt must also say the selected version.
// The update's RomFS alone has far fewer files (runtime/ui/launcher.mm, check_romfs).
#pragma once

#define GAME_VERSIONS(X)                                                   \
    X("1.8.2", "de0b6ac4efc1dda7", 1, 41056, "System/Version.txt", 1) \
    X("1.9.0", "cd57b23fa4bbad65", 1, 41062, "System/Version.txt", 1) \
    X("1.4.3", "277178b7dba1b6d4", 1, 304243, "Pack/ZsDic.pack.zs", 0)
