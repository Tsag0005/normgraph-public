"""Legacy entry point; delegates to the official-workflow adapter."""
from workflow import main

if __name__ == "__main__":
    raise SystemExit(main())
