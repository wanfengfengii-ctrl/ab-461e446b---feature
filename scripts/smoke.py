"""HTTP smoke tests against the running API.

Exercises the health endpoint and the audit endpoint end-to-end, including
a program with inch-unit (G20) relative (G91) moves and a diagonal segment
that crosses a forbidden cuboid although both endpoints are clear.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")


def http(method: str, path: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def check(name: str, condition: bool, detail="") -> None:
    if not condition:
        print(f"SMOKE FAIL: {name} {detail}")
        sys.exit(1)
    print(f"  ok: {name}")


def main() -> None:
    print(f"smoke against {BASE_URL}")

    status, body = http("GET", "/health")
    check("health returns 200 ok", status == 200 and body.get("status") == "ok")

    base_request = {
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {
            "min": {"x": -50, "y": -50, "z": -50},
            "max": {"x": 50, "y": 50, "z": 50},
        },
        "forbidden_regions": [
            {"bounds": {"min": {"x": 20, "y": 20, "z": -1},
                        "max": {"x": 30, "y": 30, "z": 1}}}
        ],
    }

    # Inch-relative moves: +1 inch on X (25.4 mm exact), then -0.5 inch on X
    # and +0.25 inch on Y. Final = (12.7, 6.35, 0) mm.
    program = "G20 G91 G0 X1\nG1 X-0.5 Y0.25"
    request = dict(base_request, program=program)
    status, body = http("POST", "/api/toolpaths/audit", request)
    check("inch-relative program accepted", status == 200, body)
    check(
        "inch conversion is exact decimal",
        body["final_position_mm"] == {"x": "12.7", "y": "6.35", "z": "0"},
        body.get("final_position_mm"),
    )
    check(
        "segments are normalized millimetres",
        body["segments"][0]["end"]["x"] == "25.4"
        and body["segments"][0]["motion"] == "G0"
        and body["segments"][1]["motion"] == "G1",
        body.get("segments"),
    )

    # Diagonal segment: endpoints (0,0) and (40,40) are clear of the box
    # [20,20]-[30,30] in xy, but the line y=x runs straight through it.
    request = dict(base_request, program="G21 G90 G0 X0 Y0\nG1 X40 Y40")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "diagonal forbidden-region crossing is rejected",
        status == 422
        and body.get("error") == "forbidden_contact"
        and body.get("line") == 2
        and body.get("forbidden_region") == 1,
        body,
    )
    check("rejection exposes no partial toolpath",
          "segments" not in body and "final_position_mm" not in body, body)

    # Coordinates before a motion mode is established.
    request = dict(base_request, program="G21 G90 X1")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "motion-before-mode error is pinned to line 1",
        status == 422 and body.get("line") == 1,
        body,
    )

    # --- controller_steps_mm: fixed-step quantization ---------------------
    steps = {"x": 0.5, "y": 0.5, "z": 0.5}

    # (a) millimetre absolute moves snap to the 0.5 mm grid.
    request = dict(base_request, controller_steps_mm=steps,
                   program="G21 G90 G0 X0.6 Y0.9\nG1 X1.2 Z0.25")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check("mm absolute program with steps accepted", status == 200, body)
    check(
        "mm absolute endpoints are on-grid",
        body["segments"][0]["end"] == {"x": "0.5", "y": "1", "z": "0"}
        and body["segments"][1]["end"] == {"x": "1", "y": "1", "z": "0.5"}
        and body["final_position_mm"] == {"x": "1", "y": "1", "z": "0.5"},
        body.get("segments"),
    )

    # (b) inch relative moves: displacement rounds per line and accumulates
    # from the previous quantized actual position. 10 mm grid:
    # +1in=25.4 -> 30; -0.5in=-12.7 -> -10, reaching 20; 0.25in on y
    # = 6.35 -> 10.
    request = dict(base_request,
                   controller_steps_mm={"x": 10, "y": 10, "z": 10},
                   program="G20 G91 G0 X1\nG1 X-0.5 Y0.25")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check("inch relative program with steps accepted", status == 200, body)
    check(
        "inch relative displacements snap then accumulate",
        body["final_position_mm"] == {"x": "20", "y": "10", "z": "0"}
        and body["segments"][0]["end"] == {"x": "30", "y": "0", "z": "0"}
        and body["segments"][1]["start"] == {"x": "30", "y": "0", "z": "0"},
        body.get("segments"),
    )

    # (c) exact half step on a negative move rounds away from zero.
    request = dict(base_request, controller_steps_mm={"x": 1, "y": 1, "z": 1},
                   program="G21 G91 G0 X-0.5\nG1 X0.25")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check("negative half-step program accepted", status == 200, body)
    check(
        "negative half step rounds away from zero",
        body["segments"][0]["end"]["x"] == "-1"
        and body["final_position_mm"]["x"] == "-1",
        body.get("segments"),
    )

    # Off-grid initial position is a 400 request error.
    bad_request = dict(base_request, controller_steps_mm=steps,
                       initial_position_mm={"x": 0.2, "y": 0, "z": 0},
                       program="G21 G90 G0 X1")
    status, body = http("POST", "/api/toolpaths/audit", bad_request)
    check("off-grid initial position rejected as request error",
          status == 400 and body.get("error") == "invalid_request", body)

    # (d) regression: omitting controller_steps_mm keeps the ideal contract.
    request = dict(base_request, program="G21 G90 G0 X0.333\nG1 X1.777")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check("ideal coordinates returned unchanged when steps omitted",
          status == 200
          and body["final_position_mm"] == {"x": "1.777", "y": "0", "z": "0"}
          and "controller_steps_mm" not in request,
          body)

    print("smoke OK")


if __name__ == "__main__":
    main()
