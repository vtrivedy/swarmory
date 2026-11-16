"""Adds tests/fake_harness.py's Fake to HARNESSES in each Python process started with this folder on PYTHONPATH, as
its file and its entry in the list would: a local run's agents in tests are fake ones."""
import fake_harness
from swarmory import harness

harness.HARNESSES.append(fake_harness.Fake())
