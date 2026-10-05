"""Unit + API tests for the toolpath audit service."""

from __future__ import annotations

import pytest

from app.audit import audit
from app.server import create_app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def run(program, regions=None, initial=(0, 0, 0),
        ws_min=(-100, -100, -100), ws_max=(100, 100, 100), steps=None):
    payload = {
        "initial_position_mm": {"x": initial[0], "y": initial[1], "z": initial[2]},
        "workspace": {"min": {"x": ws_min[0], "y": ws_min[1], "z": ws_min[2]},
                      "max": {"x": ws_max[0], "y": ws_max[1], "z": ws_max[2]}},
        "forbidden_regions": regions or [],
        "program": program,
    }
    if steps is not None:
        payload["controller_steps_mm"] = {
            "x": steps[0], "y": steps[1], "z": steps[2]}
    return audit(payload)


def box(mn, mx):
    return {"bounds": {"min": {"x": mn[0], "y": mn[1], "z": mn[2]},
                       "max": {"x": mx[0], "y": mx[1], "z": mx[2]}}}


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------

def test_basic_absolute_mm_normalizes_segments():
    ok, err = run("G21 G90 G0 X10 Y20 Z0\nG1 X10 Y30 Z5")
    assert err is None
    assert ok["status"] == "accepted"
    assert ok["segments"] == [
        {"start": {"x": "0", "y": "0", "z": "0"},
         "end": {"x": "10", "y": "20", "z": "0"}, "motion": "G0", "line": 1},
        {"start": {"x": "10", "y": "20", "z": "0"},
         "end": {"x": "10", "y": "30", "z": "5"}, "motion": "G1", "line": 2},
    ]
    assert ok["final_position_mm"] == {"x": "10", "y": "30", "z": "5"}


def test_modal_settings_persist_and_new_line_setting_applies_first():
    # G21/G90/G0 established on line 1; line 3 switches to relative before
    # its coordinates are interpreted.
    program = "G21 G90 G0 X1 Y0\n;\nG91 X4"
    ok, err = run(program)
    assert err is None
    assert ok["final_position_mm"] == {"x": "5", "y": "0", "z": "0"}


def test_inch_relative_conversion_is_exact():
    # 1 inch = 25.4 mm exactly; 0.5 inch = 12.7; modal units persist.
    program = "G20 G91 G0 X1 Y0.5\nG1 X-0.25"
    ok, err = run(program)
    assert err is None
    assert ok["segments"][0]["end"] == {"x": "25.4", "y": "12.7", "z": "0"}
    assert ok["segments"][1]["start"] == {"x": "25.4", "y": "12.7", "z": "0"}
    assert ok["final_position_mm"] == {"x": "19.05", "y": "12.7", "z": "0"}


def test_canonical_decimal_inputs_and_outputs():
    ok, err = run("G21 G90 G0 X1.0 Y2.50 Z0")
    assert err is None
    assert ok["final_position_mm"] == {"x": "1", "y": "2.5", "z": "0"}


def test_empty_and_comment_lines_produce_no_motion():
    ok, err = run("\n   ; only a comment\nG21 G90 G0 X1\n\n")
    assert err is None
    assert len(ok["segments"]) == 1
    assert ok["segments"][0]["line"] == 3


def test_workspace_boundary_is_closed():
    ok, err = run("G21 G90 G0 X10 Y10 Z10", initial=(10, 10, 10),
                  ws_min=(10, 10, 10), ws_max=(10, 10, 10))
    assert err is None
    assert ok["final_position_mm"] == {"x": "10", "y": "10", "z": "10"}


def test_zero_length_move_produces_no_segment_but_updates_nothing():
    ok, err = run("G21 G90 G0 X0 Y0 Z0")
    assert err is None
    assert ok["segments"] == []
    assert ok["final_position_mm"] == {"x": "0", "y": "0", "z": "0"}


def test_unspecified_axes_keep_their_position():
    ok, err = run("G21 G91 G0 X5\nG1 Z-2")
    assert err is None
    assert ok["final_position_mm"] == {"x": "5", "y": "0", "z": "-2"}


# ---------------------------------------------------------------------------
# program errors, each pinned to the original line
# ---------------------------------------------------------------------------

def test_coordinates_before_motion_mode():
    ok, err = run("G21 G90 X5")
    assert ok is None
    assert err["error"] == "program_error"
    assert err["line"] == 1
    assert "motion mode" in err["reason"]


def test_coordinates_before_unit_mode():
    ok, err = run("G90 G0 X5")
    assert err["line"] == 1
    assert "unit mode" in err["reason"]


def test_coordinates_before_position_mode():
    ok, err = run("G21 G0 X5")
    assert err["line"] == 1
    assert "position mode" in err["reason"]


def test_illegal_word_reports_line():
    program = "G21 G90 G0 X1\nM30\nG1 X2"
    ok, err = run(program)
    assert ok is None
    assert err["line"] == 2
    assert err["error"] == "program_error"


def test_repeated_axis_reports_line():
    ok, err = run("G21 G90 G0 X1 X2")
    assert err["line"] == 1
    assert "repeat" in err["reason"].lower()


def test_conflicting_modal_words_on_one_line():
    ok, err = run("G21 G20 G90 G0 X1")
    assert err["line"] == 1
    assert "conflict" in err["reason"]
    ok, err = run("G21 G90 G91 G0 X1")
    assert err["line"] == 1
    ok, err = run("G21 G90 G0 G1 X1")
    assert err["line"] == 1


def test_non_canonical_and_non_finite_decimals():
    ok, err = run("G21 G90 G0 X0x1")
    assert err["line"] == 1
    ok, err = run("G21 G90 G0 XNaN")
    assert err["line"] == 1
    ok, err = run("G21 G90 G0 X1_0")
    assert err["line"] == 1
    ok, err = run("G21 G90 G0 X")
    assert err["line"] == 1


def test_error_line_refers_to_first_offending_line():
    program = "G21 G90 G0 X1\nG1 X2\nbogus line here"
    # parsing happens while executing; the first bad line is reported
    ok, err = run(program)
    assert err["line"] == 3


# ---------------------------------------------------------------------------
# workspace and forbidden-region enforcement
# ---------------------------------------------------------------------------

def test_endpoint_outside_workspace_rejected_without_partial_track():
    ok, err = run("G21 G90 G0 X1\nG1 X200")
    assert ok is None
    assert "segments" not in err
    assert err["error"] == "outside_workspace"
    assert err["line"] == 2


def test_diagonal_crossing_forbidden_box_is_detected():
    # Both endpoints are well inside the workspace, but the segment slants
    # through the box — checking endpoints only would miss this.
    region = box((4, 4, -1), (6, 6, 1))
    ok, err = run("G21 G90 G0 X0 Y0\nG1 X10 Y10", regions=[region])
    assert err is not None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 2
    assert err["forbidden_region"] == 1


def test_grazing_box_boundary_is_contact_and_rejected():
    region = box((4, 5, 0), (6, 6, 0))
    ok, err = run("G21 G90 G0 X0 Y5 Z0\nG1 X10 Y5 Z0", regions=[region])
    assert err is not None
    assert err["error"] == "forbidden_contact"
    assert err["forbidden_region"] == 1


def test_path_clear_of_box_is_accepted():
    region = box((4, 7, -1), (6, 9, 1))
    ok, err = run("G21 G90 G0 X0 Y0\nG1 X10 Y10", regions=[region])
    assert err is None and ok is not None


def test_segment_starting_inside_forbidden_box_rejected():
    region = box((-1, -1, -1), (1, 1, 1))
    ok, err = run("G21 G90 G0 X5 Y5", regions=[region])
    assert err is not None
    assert err["line"] == 1


def test_first_violation_and_region_number_are_stable():
    regions = [box((40, 40, -1), (60, 60, 1)),
               box((4, 4, -1), (6, 6, 1))]
    program = "G21 G90 G0 X0 Y0\nG1 X10 Y10\nG1 X50 Y50"
    ok, err = run(program, regions=regions)
    assert err["line"] == 2
    assert err["forbidden_region"] == 2


def test_relative_move_escaping_workspace_is_caught():
    ok, err = run("G21 G91 G0 X90\nG1 X20")
    assert err["error"] == "outside_workspace"
    assert err["line"] == 2


def test_degenerate_move_inside_forbidden_region_is_rejected():
    region = box((-1, -1, -1), (1, 1, 1))
    ok, err = run("G21 G90 G0 X0 Y0 Z0", regions=[region])
    assert err is not None
    assert err["error"] == "forbidden_contact"
    assert err["forbidden_region"] == 1


def test_earlier_geometry_violation_beats_later_lexical_error():
    region = box((4, 4, -1), (6, 6, 1))
    program = "G21 G90 G0 X0 Y0\nG1 X10 Y10\nM99"
    ok, err = run(program, regions=[region])
    assert ok is None
    assert err["line"] == 2
    assert err["error"] == "forbidden_contact"


def test_when_geometry_is_clear_later_lexical_error_is_reported():
    region = box((40, 40, -1), (60, 60, 1))
    program = "G21 G90 G0 X0 Y0\nG1 X10 Y10\nM99"
    ok, err = run(program, regions=[region])
    assert ok is None
    assert err["line"] == 3


def test_exponent_decimal_and_lowercase_words_and_crlf():
    ok, err = run("g21 g90 g0 x1e2\r\nG1 Y2.5e1")
    assert err is None
    assert ok["final_position_mm"] == {"x": "100", "y": "25", "z": "0"}
    assert ok["segments"][0]["line"] == 1
    assert ok["segments"][1]["line"] == 2


def test_endpoint_exactly_on_region_boundary_rejected():
    region = box((10, 0, -1), (20, 10, 1))
    ok, err = run("G21 G90 G0 X10 Y0 Z0", regions=[region])
    assert err is not None
    assert err["error"] == "forbidden_contact"
    assert err["forbidden_region"] == 1


def test_comment_after_words_is_ignored():
    ok, err = run("G21 G90 G0 X7 ; plunge comment\n; another")
    assert err is None
    assert ok["final_position_mm"] == {"x": "7", "y": "0", "z": "0"}
    assert len(ok["segments"]) == 1


def test_packed_words_without_spaces():
    ok, err = run("G21G90G1X1Y2Z3")
    assert err is None
    assert ok["final_position_mm"] == {"x": "1", "y": "2", "z": "3"}


def test_5000_lines_is_allowed():
    program = "\n".join(["G21 G91 G0 X0.001"] * 5000)
    ok, err = run(program, ws_max=(10, 10, 10))
    assert err is None
    assert ok["final_position_mm"]["x"] == "5"


def test_empty_program_yields_empty_track():
    ok, err = run("\n; nothing\n   ; here\n")
    assert err is None
    assert ok["segments"] == []
    assert ok["final_position_mm"] == {"x": "0", "y": "0", "z": "0"}


def test_extreme_exponent_is_rejected_on_its_line():
    ok, err = run("G21 G90 G0 X1e999999")
    assert err["line"] == 1
    assert err["error"] == "program_error"


def test_signed_and_dot_leading_decimals():
    ok, err = run("G21 G90 G0 X+2 Y-.5 Z.25")
    assert err is None
    assert ok["final_position_mm"] == {"x": "2", "y": "-0.5", "z": "0.25"}


# ---------------------------------------------------------------------------
# controller_steps_mm quantization
# ---------------------------------------------------------------------------

def test_steps_mm_absolute_mm_moves_snap_to_grid():
    # step 0.5 on every axis; 0.6 -> 0.5, 0.9 -> 1, 1.2 -> 1, 0.25 -> 0.5
    ok, err = run("G21 G90 G0 X0.6 Y0.9 Z0.25\nG1 X1.2 Y0.9 Z0",
                  steps=(0.5, 0.5, 0.5))
    assert err is None, err
    assert ok["segments"][0]["start"] == {"x": "0", "y": "0", "z": "0"}
    assert ok["segments"][0]["end"] == {"x": "0.5", "y": "1", "z": "0.5"}
    assert ok["segments"][1]["start"] == {"x": "0.5", "y": "1", "z": "0.5"}
    assert ok["segments"][1]["end"] == {"x": "1", "y": "1", "z": "0"}
    assert ok["final_position_mm"] == {"x": "1", "y": "1", "z": "0"}


def test_steps_inch_relative_moves_round_displacement_then_accumulate():
    # 1 inch = 25.4 mm; with a 10 mm x step, +1in (+25.4) snaps to +30;
    # -0.5in (-12.7) snaps to -10 and accumulates from the actual 30 -> 20.
    # +0.25in on y = 6.35 mm snaps to 10 on a 10 mm y step.
    program = "G20 G91 G0 X1\nG1 X-0.5 Y0.25"
    ok, err = run(program, steps=(10, 10, 10))
    assert err is None, err
    assert ok["segments"][0]["end"] == {"x": "30", "y": "0", "z": "0"}
    assert ok["segments"][1]["start"] == {"x": "30", "y": "0", "z": "0"}
    assert ok["segments"][1]["end"] == {"x": "20", "y": "10", "z": "0"}
    assert ok["final_position_mm"] == {"x": "20", "y": "10", "z": "0"}


def test_steps_half_step_negative_rounds_away_from_zero():
    # Exact half step: +0.5 with step 1 -> +1; -0.5 -> -1 (away from zero).
    ok, err = run("G21 G91 G0 X0.5\nG1 X-1.0", steps=(1, 1, 1))
    assert err is None, err
    assert ok["segments"][0]["end"]["x"] == "1"
    assert ok["segments"][1]["end"]["x"] == "0"

    ok, err = run("G21 G91 G0 X-0.5\nG1 X0.25", steps=(1, 1, 1))
    assert err is None, err
    assert ok["segments"][0]["end"]["x"] == "-1"
    # +0.25 snaps to a zero-step displacement: the executed line is
    # degenerate, so it emits no segment and the actual position stays -1.
    assert len(ok["segments"]) == 1
    assert ok["final_position_mm"]["x"] == "-1"

    # Same tie rule for absolute targets: -0.5 on a 1 mm grid -> -1.
    ok, err = run("G21 G90 G0 X-0.5", steps=(1, 1, 1))
    assert err is None, err
    assert ok["final_position_mm"]["x"] == "-1"


def test_steps_unspecified_axes_keep_quantized_position():
    ok, err = run("G21 G91 G0 X0.6\nG1 Z0.6", steps=(0.5, 0.5, 0.5))
    assert err is None, err
    assert ok["segments"][0]["end"] == {"x": "0.5", "y": "0", "z": "0"}
    assert ok["segments"][1]["start"] == {"x": "0.5", "y": "0", "z": "0"}
    assert ok["segments"][1]["end"] == {"x": "0.5", "y": "0", "z": "0.5"}


def test_steps_quantized_path_contacting_forbidden_region_is_rejected():
    # Ideal move ends at x=4.4 (clear of the box starting at x=4.6...) but a
    # 0.5 mm grid snaps the endpoint to x=4.5 ... choose box so the ideal
    # path clears yet the executed segment touches it.
    region = box((4.5, -1, -1), (5.5, 1, 1))
    ok, err = run("G21 G90 G0 X0\nG1 X4.4", regions=[region],
                  steps=(0.5, 0.5, 0.5))
    assert ok is None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 2
    assert "segments" not in err


def test_steps_quantized_path_leaving_workspace_is_rejected():
    # Ideal accumulation 8.6 then +0.8 = 9.4 stays inside max=9; the snapped
    # path goes 9 then +1 = 10 and leaves on line 2.
    ok, err = run("G21 G91 G0 X8.6\nG1 X0.8", ws_max=(9, 9, 9),
                  steps=(1, 1, 1))
    assert ok is None
    assert err["error"] == "outside_workspace"
    assert err["line"] == 2


def test_steps_quantization_reports_earliest_violating_original_line():
    region = box((4.5, -1, -1), (5.5, 1, 1))
    program = "G21 G91 G0 X4.4\nG1 X4.4"
    ok, err = run(program, regions=[region], steps=(1, 1, 1))
    # Line 1 executes to x=4 (clear); line 2 snaps +4.4 to +4 and reaches 8,
    # crossing the box [4.5, 5.5].
    assert ok is None
    assert err["line"] == 2
    assert err["error"] == "forbidden_contact"


def test_steps_initial_position_off_grid_is_request_error():
    ok, err = run("G21 G90 G0 X1", initial=(0.2, 0, 0), steps=(0.5, 0.5, 0.5))
    assert ok is None
    assert err["error"] == "invalid_request"
    assert "step grid" in err["reason"]


def test_steps_initial_position_on_grid_accepted():
    ok, err = run("G21 G90 G0 X1.5", initial=(0.5, 0, 0),
                  steps=(0.5, 0.5, 0.5))
    assert err is None, err
    assert ok["final_position_mm"]["x"] == "1.5"


def test_steps_rejects_invalid_objects():
    def raw(steps_value):
        payload = {
            "initial_position_mm": {"x": 0, "y": 0, "z": 0},
            "workspace": {"min": {"x": -10, "y": -10, "z": -10},
                          "max": {"x": 10, "y": 10, "z": 10}},
            "forbidden_regions": [],
            "program": "G21 G90 G0 X1",
            "controller_steps_mm": steps_value,
        }
        return audit(payload)

    ok, err = raw([0.5, 0.5, 0.5])
    assert err["error"] == "invalid_request"
    ok, err = raw({"x": 0.5, "y": 0.5})
    assert err["error"] == "invalid_request"
    ok, err = raw({"x": 0, "y": 0.5, "z": 0.5})
    assert err["error"] == "invalid_request"
    ok, err = raw({"x": -0.5, "y": 0.5, "z": 0.5})
    assert err["error"] == "invalid_request"
    ok, err = raw({"x": "NaN", "y": 0.5, "z": 0.5})
    assert err["error"] == "invalid_request"


def test_steps_omitted_keeps_ideal_contract_unchanged():
    # Regression: without controller_steps_mm the ideal coordinates are
    # returned verbatim even when they would not lie on any step grid.
    ok, err = run("G21 G90 G0 X0.333\nG1 X1.777")
    assert err is None
    assert ok["final_position_mm"] == {"x": "1.777", "y": "0", "z": "0"}
    assert ok["segments"][0]["end"]["x"] == "0.333"


# ---------------------------------------------------------------------------
# request validation
# ---------------------------------------------------------------------------

def test_too_many_regions():
    ok, err = run("G21 G90 G0 X1", regions=[box((0, 0, 0), (1, 1, 1))] * 21)
    assert ok is None
    assert err["error"] == "invalid_request"


def test_too_many_lines():
    program = "\n".join(["G21 G90 G0 X0"] * 5001)
    ok, err = run(program)
    assert err["error"] == "invalid_request"


def test_initial_position_outside_workspace():
    ok, err = run("G21 G90 G0 X1", initial=(500, 0, 0))
    assert err["error"] == "invalid_request"


def test_non_finite_json_number_rejected():
    payload = {
        "initial_position_mm": {"x": "NaN", "y": 0, "z": 0},
        "workspace": {"min": {"x": -10, "y": -10, "z": -10},
                      "max": {"x": 10, "y": 10, "z": 10}},
        "forbidden_regions": [],
        "program": "G21 G90 G0 X1",
    }
    ok, err = audit(payload)
    assert err["error"] == "invalid_request"


def test_failure_never_returns_segments():
    ok, err = run("G21 G90 G0 X5\nG1 X1000")
    assert ok is None
    assert "segments" not in err
    assert "final_position_mm" not in err


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    return create_app().test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_audit_endpoint_success(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": 0, "y": 0, "z": 0},
                      "max": {"x": 100, "y": 100, "z": 100}},
        "forbidden_regions": [],
        "program": "G21 G90 G0 X1",
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "accepted"


def test_audit_endpoint_program_error_status(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": 0, "y": 0, "z": 0},
                      "max": {"x": 100, "y": 100, "z": 100}},
        "forbidden_regions": [],
        "program": "X1",
    })
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["line"] == 1
    assert "segments" not in body


def test_audit_endpoint_steps_mm_snaps_and_off_grid_rejected(client):
    payload = {
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": -10, "y": -10, "z": -10},
                      "max": {"x": 10, "y": 10, "z": 10}},
        "forbidden_regions": [],
        "controller_steps_mm": {"x": 1, "y": 1, "z": 1},
        "program": "G21 G91 G0 X0.5\nG1 X-1.5",
    }
    resp = client.post("/api/toolpaths/audit", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    # +0.5 half step -> +1; -1.5 -> -2 accumulated from 1 -> -1.
    assert body["segments"][0]["end"]["x"] == "1"
    assert body["final_position_mm"]["x"] == "-1"

    payload["initial_position_mm"] = {"x": 0.3, "y": 0, "z": 0}
    resp = client.post("/api/toolpaths/audit", json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_request"


def test_audit_endpoint_malformed_json(client):
    resp = client.post("/api/toolpaths/audit", data="not json",
                       content_type="application/json")
    assert resp.status_code == 400
