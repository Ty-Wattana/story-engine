"""Engine package — game loop, state coordination, and character creation."""

# Public API surface: callers use main() for full bootstrap or
# game_loop() / initialize_game() when composing manually.
from src.engine.loop import game_loop, main
from src.engine.creation import initialize_game

__all__ = ["game_loop", "main", "initialize_game"]
