#!/usr/bin/env python3
"""Emit one bounded, read-only performance snapshot of the running Kodi."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import time


SAMPLE_SECONDS = 1.0
RPC_ADDRESS = ("127.0.0.1", 9090)
RPC_TIMEOUT_SECONDS = 2.0
KODI_LOG = Path("/home/htpc/.kodi/temp/kodi.log")
THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")


class SnapshotError(RuntimeError):
    pass


def parse_kib_fields(payload):
    values = {}
    for line in payload.splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        fields = remainder.split()
        if fields and fields[0].isdigit():
            values[key] = int(fields[0])
    return values


def parse_task_stat(payload):
    closing = payload.rfind(")")
    if not payload.startswith("(") and " (" not in payload:
        raise SnapshotError("task stat has no command")
    if closing < 0:
        raise SnapshotError("task stat has no closing command delimiter")
    opening = payload.find("(")
    fields = payload[closing + 2 :].split()
    if len(fields) < 13:
        raise SnapshotError("task stat is incomplete")
    return payload[opening + 1 : closing], int(fields[11]) + int(fields[12])


def find_kodi_pid(proc=Path("/proc")):
    matches = []
    for candidate in proc.iterdir():
        if not candidate.name.isdigit():
            continue
        try:
            command = (candidate / "comm").read_text().strip()
        except (OSError, UnicodeError):
            continue
        if command == "kodi.bin":
            matches.append(int(candidate.name))
    if len(matches) != 1:
        raise SnapshotError("expected exactly one kodi.bin process")
    return matches[0]


def read_cpu_ticks(proc, pid):
    process = proc / str(pid)
    tasks = {}
    for task in (process / "task").iterdir():
        if not task.name.isdigit():
            continue
        try:
            name, ticks = parse_task_stat((task / "stat").read_text())
        except (OSError, UnicodeError, ValueError, SnapshotError):
            continue
        tasks[int(task.name)] = (name, ticks)
    cpu_fields = (proc / "stat").read_text().splitlines()[0].split()[1:]
    total = sum(int(value) for value in cpu_fields)
    return total, tasks


def cpu_percentages(before, after, cpu_count):
    total_delta = after[0] - before[0]
    if total_delta <= 0:
        raise SnapshotError("system CPU counters did not advance")
    rows = []
    for thread_id, (name, ticks) in after[1].items():
        previous = before[1].get(thread_id)
        if previous is None:
            continue
        delta = ticks - previous[1]
        if delta <= 0:
            continue
        rows.append(
            {
                "tid": thread_id,
                "name": name,
                "cpu_percent": round(
                    100.0 * float(delta) * cpu_count / total_delta,
                    2,
                ),
            }
        )
    return sorted(rows, key=lambda row: row["cpu_percent"], reverse=True)


def kodi_gui_state():
    request = {
        "jsonrpc": "2.0",
        "id": "htpc-performance",
        "method": "GUI.GetProperties",
        "params": {"properties": ["currentwindow", "currentcontrol"]},
    }
    payload = (json.dumps(request, separators=(",", ":")) + "\n").encode()
    with socket.create_connection(RPC_ADDRESS, RPC_TIMEOUT_SECONDS) as stream:
        stream.settimeout(RPC_TIMEOUT_SECONDS)
        stream.sendall(payload)
        decoder = json.JSONDecoder()
        text = ""
        while len(text) <= 64 * 1024:
            chunk = stream.recv(4096)
            if not chunk:
                break
            text += chunk.decode("utf-8")
            try:
                response, _end = decoder.raw_decode(text.lstrip())
            except ValueError:
                continue
            if response.get("id") != request["id"] or "result" not in response:
                raise SnapshotError("Kodi returned an invalid GUI response")
            return response["result"]
    raise SnapshotError("Kodi returned no complete GUI response")


def snapshot(label=""):
    proc = Path("/proc")
    pid = find_kodi_pid(proc)
    before = read_cpu_ticks(proc, pid)
    time.sleep(SAMPLE_SECONDS)
    after = read_cpu_ticks(proc, pid)
    status = parse_kib_fields((proc / str(pid) / "status").read_text())
    memory = parse_kib_fields((proc / "meminfo").read_text())
    temperature = None
    try:
        temperature = round(float(THERMAL_ZONE.read_text()) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    try:
        log_bytes = KODI_LOG.stat().st_size
    except OSError:
        log_bytes = None
    threads = cpu_percentages(before, after, os.cpu_count() or 1)
    return {
        "schema": 1,
        "label": label,
        "sample_seconds": SAMPLE_SECONDS,
        "kodi": {
            "pid": pid,
            "rss_kib": status.get("VmRSS"),
            "thread_count": status.get("Threads"),
            "cpu_percent": round(
                sum(row["cpu_percent"] for row in threads), 2
            ),
            "top_threads": threads[:10],
            "gui": kodi_gui_state(),
            "log_bytes": log_bytes,
        },
        "system": {
            "memory_available_kib": memory.get("MemAvailable"),
            "temperature_c": temperature,
        },
    }


def main(arguments=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="")
    options = parser.parse_args(arguments)
    print(json.dumps(snapshot(options.label), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
