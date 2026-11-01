"""
Breakpoint Engine for Indicator Studio Replay Session.
Supports 'Break on Signal', variable threshold triggers, and custom conditional hooks.
Freezes execution instantly at the exact candle where criteria are satisfied.
"""
from typing import Callable, List, Optional, Any, Dict
from dataclasses import dataclass


@dataclass
class BreakpointRule:
    name: str
    condition: Callable[[Any], bool]
    enabled: bool = True


class BreakpointManager:
    """
    Manages active breakpoints and evaluates them against each replay frame.
    """

    def __init__(self, break_on_signal: bool = True):
        self.break_on_signal = break_on_signal
        self.rules: List[BreakpointRule] = []

    def add_rule(self, name: str, condition: Callable[[Any], bool]):
        """Adds a custom condition breakpoint."""
        self.rules.append(BreakpointRule(name=name, condition=condition, enabled=True))

    def remove_rule(self, name: str):
        """Removes a rule by name."""
        self.rules = [r for r in self.rules if r.name != name]

    def clear_rules(self):
        """Clears all custom rules."""
        self.rules.clear()

    def evaluate(self, frame: Any) -> Optional[str]:
        """
        Evaluates active breakpoints against the given ReplayFrame.
        Returns the breakpoint trigger reason string if hit, or None.
        """
        # 1. Break on Signal check
        if self.break_on_signal and getattr(frame, "active_signal", None) is not None:
            sig = frame.active_signal
            if getattr(sig, "action", None) in ("BUY_CE", "BUY_PE", "EXIT_CE", "EXIT_PE"):
                return f"Signal Fired: {sig.action} @ {getattr(sig, 'trigger_price', 0.0)}"

        # 2. Custom rules check
        for rule in self.rules:
            if rule.enabled:
                try:
                    if rule.condition(frame):
                        return f"Breakpoint Hit: {rule.name}"
                except Exception:
                    pass

        return None
