#!/usr/bin/env python3
"""Copy a tool result verbatim from the session transcript (or a saved result file) into raw/.

Never retype data: this reads the exact bytes the tool returned.

Usage:
  python3 tools/save_tool_result.py --out raw/totals.json --match '"actions_lead"' [--match '"last_11d"']
      -> finds the most recent get_data tool call whose INPUT JSON contains every --match string
         and writes its result text to --out.
  python3 tools/save_tool_result.py --out raw/x.json --file /path/to/saved-result.txt
      -> copies a tool-result file that the harness saved because it was too large.
Options:
  --tool NAME   substring of the tool name (default: get_data)
  --transcript  path to a transcript .jsonl (default: newest under ~/.claude/projects)
"""
import argparse, glob, json, os, shutil, sys


def newest_transcript():
    files = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    if not files:
        sys.exit("no transcript found")
    return max(files, key=os.path.getmtime)


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return json.dumps(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--match", action="append", default=[])
    ap.add_argument("--tool", default="get_data")
    ap.add_argument("--file")
    ap.add_argument("--transcript")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)

    if a.file:
        text = open(a.file, encoding="utf-8").read()
    else:
        uses, results = {}, {}
        for line in open(a.transcript or newest_transcript(), encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            c = (d.get("message") or {}).get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if b.get("type") == "tool_use" and a.tool in b.get("name", ""):
                    uses[b["id"]] = json.dumps(b.get("input", {}))
                elif b.get("type") == "tool_result":
                    results[b["tool_use_id"]] = result_text(b.get("content"))
        cands = [i for i, inp in uses.items() if all(m in inp for m in a.match) and i in results]
        if not cands:
            sys.exit("no matching tool result for %s" % a.match)
        text = results[cands[-1]]
        # Harness may replace big outputs with a pointer to a saved file.
        if "saved to" in text[:400] and not text.lstrip().startswith(("{", "[")):
            import re
            m = re.search(r"(/\S+\.(?:txt|json))", text)
            if m and os.path.exists(m.group(1)):
                text = open(m.group(1), encoding="utf-8").read()
    try:
        data = json.loads(text)
    except ValueError:
        sys.exit("result is not JSON; refusing to save: " + text[:200])
    if isinstance(data, list) and data and isinstance(data[0], dict) and "text" in data[0]:
        data = json.loads(data[0]["text"])  # MCP content-block wrapper
    rows = data.get("result", data) if isinstance(data, dict) else data
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("saved %s (%d rows)" % (a.out, len(rows) if isinstance(rows, list) else -1))


if __name__ == "__main__":
    main()
