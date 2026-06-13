"""Story Engine — entry point."""

import sys
import os

sys.path.insert(0, os.path.abspath("."))

from src.engine.loop import main

if __name__ == "__main__":
    main()
