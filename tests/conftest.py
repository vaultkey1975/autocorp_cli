"""Pytest bootstrap for the AutoCorp CLI self-test suite.

Puts the project root on sys.path so the tests can import the project packages
(brains, core, safety, memory) no matter which directory pytest is invoked from.
This is belt-and-suspenders alongside `pythonpath = .` in pytest.ini.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
