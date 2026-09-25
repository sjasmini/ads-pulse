#!/usr/bin/env python3
"""Decode a Google Drive download_file_content result (exportMimeType text/csv) into raw/sheets/<name>.csv.

Copies the tool result from the session transcript (or a saved result file) and base64-decodes it —
the CSV is never retyped.

  python3 tools/save_drive_csv.py --name funnel --match '<fileId>'
  python3 tools/save_drive_csv.py --name creative --file /path/to/saved-result.txt
Refuses to write a CSV whose header has personal-data columns (name / phone / email)."""
import argparse, base64, binascii, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from save_tool_result import newest_transcript, result_text  # noqa: E402

PII = re.compile(r"(?i)^\s*((first|last|full|lead|contact|student|owner)?[ _]?name|.*e-?mail.*|.*phone.*|.*mobile.*)\s*$")


def find_payload(obj):
    """Return the base64 (or plain CSV) string inside a tool result of unknown shape."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        for x in obj:
            p = find_payload(x)
            if p:
                return p
    if isinstance(obj, dict):
        for k in ("content", "data", "base64", "blob", "text", "file_content", "result"):
            if k in obj:
                p = find_payload(obj[k])
                if p:
                    return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--match", action="append", default=[])
    ap.add_argument("--file")
    a = ap.parse_args()
    if a.file:
        text = open(a.file, encoding="utf-8").read()
    else:
        uses, results = {}, {}
        for line in open(newest_transcript(), encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            c = (d.get("message") or {}).get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if b.get("type") == "tool_use" and "download_file_content" in b.get("name", ""):
                    uses[b["id"]] = json.dumps(b.get("input", {}))
                elif b.get("type") == "tool_result":
                    results[b["tool_use_id"]] = result_text(b.get("content"))
        ids = [i for i, inp in uses.items() if all(m in inp for m in a.match) and i in results]
        if not ids:
            sys.exit("no matching download_file_content result")
        text = results[ids[-1]]
        m = re.search(r"(/\S+\.(?:txt|json))", text[:600])
        if m and os.path.exists(m.group(1)) and not text.lstrip().startswith("{"):
            text = open(m.group(1), encoding="utf-8").read()
    try:
        payload = find_payload(json.loads(text))
    except ValueError:
        payload = text
    payload = (payload or "").strip()
    try:
        csv_text = base64.b64decode(payload, validate=True).decode("utf-8-sig")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        csv_text = payload  # already plain CSV
    header = csv_text.splitlines()[0] if csv_text else ""
    if not header or "," not in header:
        sys.exit("decoded content does not look like CSV")
    import csv as _csv, io as _io
    cols = next(_csv.reader(_io.StringIO(header)))
    if any(PII.match(c) for c in cols):
        sys.exit("refusing: header has personal-data columns — export a tab without names/phones/emails")
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "raw", "sheets", a.name + ".csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(csv_text)
    print(f"saved {out} ({len(csv_text.splitlines()) - 1} rows)")


if __name__ == "__main__":
    main()
