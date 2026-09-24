"""Compatibility import for the standalone test tools."""
import sys
from engine.speech import mixed as _implementation
sys.modules[__name__] = _implementation
