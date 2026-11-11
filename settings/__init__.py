"""Settings: JSON podešavanja aplikacije i centralni menadžer tema."""

from .settings_manager import DEFAULTS, SettingsManager, default_data_dir
from .theme_manager import ACCENTS, THEMES, ThemeManager

__all__ = ["SettingsManager", "ThemeManager", "DEFAULTS", "THEMES", "ACCENTS", "default_data_dir"]
