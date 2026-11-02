"""Fetch pinned Vega browser bundles, verify npm integrity and retain licenses."""

import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen


PACKAGES = {"vega": "6.4.0", "vega-lite": "6.5.0", "vega-embed": "7.3.0"}
DESTINATION = Path(__file__).resolve().parents[1] / "apps/web/app/vendor"


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, version in PACKAGES.items():
        with urlopen(f"https://registry.npmjs.org/{name}/{version}", timeout=30) as response:
            metadata = json.load(response)
        dist = metadata["dist"]
        with urlopen(dist["tarball"], timeout=30) as response:
            archive = response.read()
        algorithm, expected = dist["integrity"].split("-", 1)
        actual = base64.b64encode(hashlib.new(algorithm, archive).digest()).decode()
        if actual != expected:
            raise ValueError(f"Integrity mismatch: {name}")
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
            bundle = package.extractfile(f"package/build/{name}.min.js").read()
            license_text = package.extractfile("package/LICENSE").read()
        filename = f"{name}-{version}.min.js"
        (DESTINATION / filename).write_bytes(bundle)
        (DESTINATION / f"{name}-LICENSE").write_bytes(license_text)
        manifest.append({"name": name, "version": version, "file": filename,
                         "source": dist["tarball"], "integrity": dist["integrity"],
                         "sha256": hashlib.sha256(bundle).hexdigest()})
    (DESTINATION / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Vendored: " + ", ".join(f"{name}@{version}" for name, version in PACKAGES.items()))


if __name__ == "__main__":
    main()
