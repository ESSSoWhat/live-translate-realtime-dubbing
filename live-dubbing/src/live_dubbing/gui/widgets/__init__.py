"""
GUI widgets module.
"""

from live_dubbing.gui.widgets.app_selector import AppSelectorWidget
from live_dubbing.gui.widgets.audio_meter import AudioMeter
from live_dubbing.gui.widgets.debug_window import DebugWindow
from live_dubbing.gui.widgets.language_panel import LanguagePanel
from live_dubbing.gui.widgets.status_bar import StatusBar
from live_dubbing.gui.widgets.vb_cable_wizard import VBCableSetupWizard

__all__ = [
    "AppSelectorWidget",
    "LanguagePanel",
    "StatusBar",
    "AudioMeter",
    "DebugWindow",
    "VBCableSetupWizard",
]
