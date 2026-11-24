"""Project-wide settings. ROOT is the project folder: every data file (CSVs, ledgers, history/, caches, .env) lives
there, wherever the code sits."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
