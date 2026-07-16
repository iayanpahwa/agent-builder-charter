#!/usr/bin/env python3
"""
Validate one charter.yaml against the schema. Exit non-zero if invalid.

Used by CI and by the new-agent skill, which loops on this until a generated
charter passes. Reuses the loader's fail-closed checks so a charter that would
be refused at runtime is also refused here.

Usage:  python3 validate.py path/to/charter.yaml
"""

import sys

from loader import CharterInvalid, load_charter


def main():
    if len(sys.argv) != 2:
        print("usage: python3 validate.py <charter.yaml>")
        sys.exit(2)
    path = sys.argv[1]
    try:
        charter = load_charter(path)
    except CharterInvalid as e:
        print(f"INVALID: {e}")
        sys.exit(1)
    print(f"VALID: {charter['id']} v{charter['version']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
