"""
Pre-Packaged Indicator Templates Registry & Presets Manager.
Enables instant discovery and loading of reference indicators into Monaco Studio IDE.
"""
from typing import List, Dict, Any
from pathlib import Path


class PresetsManager:
    """
    Manages catalog of production-ready reference indicators.
    """

    @staticmethod
    def list_presets() -> List[Dict[str, Any]]:
        """Returns catalog of available reference presets."""
        return [
            {
                "id": "bss_pro_v5",
                "name": "BSS Pro Scalper v5 (Frozen Golden Baseline)",
                "description": "Frozen benchmark version: Dynamic Body S/R, Dual EMAs (21/50), Pressure (>58%), Pullbacks & Breakouts.",
                "author": "Antigravity Quant Team",
                "file": "bss_pro_v5.py"
            },
            {
                "id": "bss_pro_v6",
                "name": "BSS Pro Scalper v6 (Active Strategy Enhancement)",
                "description": "Enhanced production candidate: Post-distribution cooldown filter and refined stop loss architecture.",
                "author": "Antigravity Quant Team",
                "file": "bss_pro_v6.py"
            },
            {
                "id": "algoalpha_breakout",
                "name": "Smart Money Breakout Channels [AlgoAlpha]",
                "description": "Dynamic volume consolidation box breakouts with ATR volatility expansion.",
                "author": "AlgoAlpha",
                "file": "algoalpha_breakout.py"
            },
            {
                "id": "supertrend_cloud",
                "name": "Supertrend Cloud & Dynamic ATR Trailing",
                "description": "Multi-timeframe trend continuation with dynamic ATR trailing stops.",
                "author": "Antigravity Quant Team",
                "file": "supertrend_cloud.py"
            }
        ]

    @staticmethod
    def get_preset_code(preset_id: str) -> str:
        """Reads and returns the Python source code for a preset."""
        current_dir = Path(__file__).resolve().parent
        file_map = {
            "bss_pro_v5": current_dir / "bss_pro_v5.py",
            "bss_pro_v6": current_dir / "bss_pro_v6.py",
            "algoalpha_breakout": current_dir / "algoalpha_breakout.py",
            "supertrend_cloud": current_dir / "supertrend_cloud.py"
        }
        target_path = file_map.get(preset_id)
        if target_path and target_path.exists():
            return target_path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Preset script '{preset_id}' not found at {target_path}.")
