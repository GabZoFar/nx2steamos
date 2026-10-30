# porter: the port pipeline. A player's game files (NSP / XCI) + their prod.keys + a game profile → a game folder that runs natively on ARM64 SteamOS, in Steam's library
# (docs/ARCHITECTURE.md). Adapted from nx2android's porter (MIT): the steps up to the shader translation are the same, the host, game folder and install are SteamOS's.
#   python -m porter doctor | profiles | inspect <folder> | port <folder> -k <prod.keys> | install <game folder> | device | steam | tools | config
__version__ = '0.1.0'
