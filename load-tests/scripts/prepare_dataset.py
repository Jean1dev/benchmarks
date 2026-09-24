#!/usr/bin/env python3
"""Prepare encrypted IDs outside the measured Gatling window."""
import argparse, csv, json, os, pathlib, time, urllib.request, uuid

def payload(size, seed):
    prefix = f"dataset-{seed}-"
    unit = "ação-日本語-😀|"
    value = prefix
    while len(value.encode()) < size: value += unit
    raw = value.encode()[:size]
    return raw.decode("utf-8") if len(raw.decode("utf-8").encode()) == size else payload(size, seed + "x")

def call(base, message, timeout):
    request = urllib.request.Request(base + "/messages", data=json.dumps({"message": message}, ensure_ascii=False).encode(), headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read())
        if response.status != 201 or not isinstance(body.get("id"), str): raise RuntimeError(body)
        return str(uuid.UUID(body["id"]))

def main():
    p = argparse.ArgumentParser(); p.add_argument("--base-url", required=True); p.add_argument("--size", type=int, choices=[128,1024,16384], required=True); p.add_argument("--count", type=int, default=100000); p.add_argument("--out", required=True); p.add_argument("--seed", default="orca-benchmark-2026-09"); p.add_argument("--timeout", type=float, default=10); args=p.parse_args()
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    started=time.time();
    with open(args.out, "w", newline="") as stream:
        writer=csv.writer(stream); writer.writerow(["id"])
        for index in range(args.count): writer.writerow([call(args.base_url, payload(args.size, f"{args.seed}-{index}"), args.timeout)])
    print(json.dumps({"count":args.count,"payload_bytes":args.size,"duration_seconds":time.time()-started,"ids_file":args.out}))
if __name__ == "__main__": main()
