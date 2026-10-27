"""Fetch exactly the reviewed MiniMax H3 skill sources, never a moving branch."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

COMMIT = "d21241f0a4b3acbb34c97dae47fa417b7065e438"
REPOSITORY = "MiniMax-AI/MiniMax-H3"
ROOT = Path(__file__).resolve().parents[1]


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ai-library-vendor"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read()


def main():
    tree = json.loads(fetch(f"https://api.github.com/repos/{REPOSITORY}/git/trees/{COMMIT}?recursive=1"))
    files = [item for item in tree["tree"] if item["type"] == "blob" and item["path"].startswith("skills/")]
    def download(item):
        data = fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{item['path']}")
        actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if actual != item["sha"]:
            raise ValueError(f"Git object hash mismatch: {item['path']}")
        target = ROOT / "presets" / "skills" / "minimax-h3" / item["path"].removeprefix("skills/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"path": item["path"], "sha256": hashlib.sha256(data).hexdigest()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        checksums = list(pool.map(download, files))
    (ROOT / "presets" / "minimax-source.json").write_text(json.dumps({
        "repository": REPOSITORY, "commit": COMMIT, "license": None,
        "files": sorted(checksums, key=lambda f: f["path"]),
    }, indent=2) + "\n")
    print(f"Vendored {len(files)} files from {REPOSITORY}@{COMMIT}")


if __name__ == "__main__":
    main()
