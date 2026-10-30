# Where things are: the repository (and the nxnative engine in it), the player's own folders (settings, toolchains, the shader translator).
import os, sys
from pathlib import Path

REPO = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent   # a bundle: nxnative/ beside the executable
NXNATIVE = REPO / 'nxnative'
DM = NXNATIVE / 'dm'
LOCAL_TOOLS = REPO / 'tools'   # a developer's toolchains next to the repository (not committed); the player installs their own


def _base(kind):
    if os.environ.get('NX2S_HOME'): return Path(os.environ['NX2S_HOME'])   # one folder for everything (tests, a portable setup)
    if os.name == 'nt': return Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming') / 'nx2steamos'
    if sys.platform == 'darwin': return Path.home() / 'Library/Application Support/nx2steamos'
    xdg = {'config': ('XDG_CONFIG_HOME', '.config'), 'data': ('XDG_DATA_HOME', '.local/share')}[kind]
    return Path(os.environ.get(xdg[0]) or Path.home() / xdg[1]) / 'nx2steamos'


def user_dir():
    """The player's settings folder: %APPDATA%\\nx2steamos on Windows, ~/Library/Application Support/nx2steamos on a Mac, $XDG_CONFIG_HOME/nx2steamos on Linux
    (SteamOS: ~/.config/nx2steamos). NX2S_HOME overrides it (tests)"""
    d = _base('config'); d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir():
    """Large per-player files: the toolchains and the shader translator ($XDG_DATA_HOME/nx2steamos on Linux, the settings folder elsewhere)"""
    d = _base('data'); d.mkdir(parents=True, exist_ok=True)
    return d


def player_tools():   # the toolchains `porter tools --download` fetched for the player: zig/, dotnet/
    return data_dir() / 'toolchains'
