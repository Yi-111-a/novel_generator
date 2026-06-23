"""Generate and accept a fixed number of chapters through the running API."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def request_json(url: str, method: str = "GET", body: dict | None = None, timeout: int = 1200):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = f"{args.base_url}/api/projects/{args.project_id}"

    status, accepted = request_json(base + "/chapters/accepted")
    if status != 200:
        raise SystemExit(f"cannot read accepted chapters: {status} {accepted}")
    start_count = len(accepted)
    print(f"[start] accepted={start_count} target_add={args.count}", flush=True)

    for index in range(args.count):
        started = time.time()
        status, draft = request_json(
            base + "/chapters/drafts",
            method="POST",
            body={"mode": "manual"},
        )
        if status != 200 or not isinstance(draft, dict):
            raise SystemExit(f"draft failed: {status} {draft}")
        combined = (draft.get("contextSnapshot") or {}).get("combinedAudit") or {}
        violations = list(combined.get("violations") or [])
        p0 = [row for row in violations if row.get("severity") == "P0"]
        print(
            f"[draft] chapter={draft.get('chapterNo')} id={draft.get('id')} "
            f"status={draft.get('status')} chars={len(draft.get('prose') or '')} "
            f"decision={combined.get('decision')} p0={len(p0)} "
            f"elapsed={time.time() - started:.1f}s",
            flush=True,
        )
        if draft.get("status") == "blocked" or combined.get("decision") != "accept":
            print(json.dumps(p0 or violations, ensure_ascii=False, indent=2), flush=True)
            return 2
        status, result = request_json(
            base + f"/chapters/drafts/{draft['id']}/accept",
            method="POST",
            body={},
        )
        if status != 200:
            raise SystemExit(f"accept failed: {status} {result}")
        print(
            f"[accepted] chapter={result.get('chapterNo')} title={result.get('title')}",
            flush=True,
        )

    status, accepted = request_json(base + "/chapters/accepted")
    print(f"[done] accepted={len(accepted) if status == 200 else '?'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
