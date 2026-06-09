from __future__ import annotations

import sys
from pathlib import Path

from payments.providers.manifest import ManifestValidationError, load_manifest


def main(paths: list[str]) -> int:
    errors = 0
    for p in paths:
        try:
            m = load_manifest(p)
        except ManifestValidationError as e:
            print(f"FAIL  {p}: {e}")
            errors += 1
        else:
            print(f"OK    {p}  ({m.provider.code} {m.provider.version})")
    return 1 if errors else 0


if __name__ == "__main__":
    paths = sys.argv[1:] or [str(p) for p in Path("manifests").glob("*.yaml")]
    sys.exit(main(paths))
