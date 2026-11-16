"""Prints the first 16 hex digits of a file's sha256.

    python3 tools/sha.py PATH
"""
import hashlib
import sys

print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()[:16])
