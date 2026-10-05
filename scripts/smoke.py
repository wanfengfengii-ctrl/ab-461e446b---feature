"""HTTP smoke tests against the running API.

Exercises the health endpoint and the audit endpoint end-to-end, including
a program with inch-unit (G20) relative (G91) moves, a diagonal segment
that crosses a forbidden cuboid although both endpoints are clear, and the
optional controller_steps_mm quantization (mm absolute, inch relative,
half-step rounding, fixture contact after quantization, off-grid initial
position, plus the no-option regression).
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

    # --- controller_steps_mm: quantized, controller-executable trajectory ---

    steps = {"x": "1", "y": "1", "z": "1"}

    # Regression: omitting the option keeps the legacy ideal-coordinate
    # contract (the inch-relative checks above also ran without it).
    request = dict(base_request, program="G21 G91 G0 X0.6\nG1 X0.6")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "without steps, ideal coordinates are returned",
        status == 200 and body["final_position_mm"]["x"] == "1.2",
        body,
    )

    # Millimetre absolute motion quantized to a per-axis step grid:
    # 10.3/0.5 -> 10.5, 20.4 -> 20, 2.9/2 -> 2.
    request = dict(base_request,
                   controller_steps_mm={"x": "0.5", "y": "1", "z": "2"},
                   program="G21 G90 G0 X10.3 Y20.4 Z2.9")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "mm absolute motion is quantized to the step grid",
        status == 200
        and body["final_position_mm"] == {"x": "10.5", "y": "20", "z": "2"},
        body,
    )

    # Inch relative motion: 0.5 in = 12.7 mm -> 13, -0.25 in = -6.35 -> -6;
    # the second move accumulates from the quantized position: 13 - 6 = 7.
    request = dict(base_request, controller_steps_mm=steps,
                   program="G20 G91 G0 X0.5\nG1 X-0.25")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "inch relative motion quantizes and accumulates from quantized position",
        status == 200
        and body["segments"][0]["end"]["x"] == "13"
        and body["final_position_mm"] == {"x": "7", "y": "0", "z": "0"},
        body,
    )

    # Exactly half a step rounds away from zero: -2.5 -> -3.
    request = dict(base_request, controller_steps_mm=steps,
                   program="G21 G91 G0 X-2.5")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "negative half step rounds away from zero",
        status == 200 and body["final_position_mm"]["x"] == "-3",
        body,
    )

    # The ideal path clears the fixture corner (20,20,0); the quantized path
    # touches it and must be rejected on the original line.
    request = dict(base_request, program="G21 G90 G0 X19.6 Y20")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check("ideal path clears the fixture", status == 200, body)
    request = dict(base_request, controller_steps_mm=steps,
                   program="G21 G90 G0 X19.6 Y20")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "quantized path contacting the fixture is rejected",
        status == 422
        and body.get("error") == "forbidden_contact"
        and body.get("line") == 1
        and body.get("forbidden_region") == 1
        and "segments" not in body,
        body,
    )

    # Initial position off the step grid is a request error.
    request = dict(base_request, controller_steps_mm=steps,
                   initial_position_mm={"x": "0.3", "y": 0, "z": 0},
                   program="G21 G90 G0 X1")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "off-grid initial position is a 400 invalid_request",
        status == 400 and body.get("error") == "invalid_request",
        body,
    )

    print("smoke OK")


if __name__ == "__main__":
    main()
