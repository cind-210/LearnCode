"""Compatibility entrypoint for `python -m src.main`."""
from .web.main import app, main

if __name__ == "__main__":
    main()
