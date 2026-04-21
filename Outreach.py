# Outreach(1).py
import asyncio
import sys
import os

# Ensure the current directory is in the path for modular imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from outreach.engine import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[CTRL+C] Exiting cleanly.")
    except Exception as e:
        print(f"\n[FATAL] {e}")
        sys.exit(1)
