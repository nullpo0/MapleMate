"""Shared entry point; bootloader distinguishes GUI and stdio executables."""
from maplemate.cli import main

if __name__ == "__main__":
    main()
