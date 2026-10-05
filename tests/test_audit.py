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
        payload["controller_steps_mm"] = {"x": steps[0], "y": steps[1],
                                          "z": steps[2]}
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
# controller_steps_mm: quantization to the controller step grid
# ---------------------------------------------------------------------------

def test_steps_mm_absolute_motion_quantized():
    # 10.3/0.5 = 20.6 -> 21 steps; 20.4 -> 20; 2.9/2 = 1.45 -> 1 step.
    ok, err = run("G21 G90 G0 X10.3 Y20.4 Z2.9", steps=("0.5", "1", "2"))
    assert err is None
    assert ok["segments"] == [
        {"start": {"x": "0", "y": "0", "z": "0"},
         "end": {"x": "10.5", "y": "20", "z": "2"}, "motion": "G0", "line": 1},
    ]
    assert ok["final_position_mm"] == {"x": "10.5", "y": "20", "z": "2"}


def test_steps_inch_relative_motion_quantized():
    # 0.5 in = 12.7 mm -> 13; 0.25 in = 6.35 mm -> 6; -0.25 in -> -6.
    ok, err = run("G20 G91 G0 X0.5 Y0.25\nG1 X-0.25", steps=(1, 1, 1))
    assert err is None
    assert ok["segments"][0]["end"] == {"x": "13", "y": "6", "z": "0"}
    assert ok["segments"][1]["start"] == {"x": "13", "y": "6", "z": "0"}
    assert ok["segments"][1]["end"] == {"x": "7", "y": "6", "z": "0"}
    assert ok["final_position_mm"] == {"x": "7", "y": "6", "z": "0"}


def test_steps_half_step_rounds_away_from_zero():
    ok, err = run("G21 G91 G0 X-0.5", initial=(10, 0, 0), steps=(1, 1, 1))
    assert err is None
    assert ok["final_position_mm"]["x"] == "9"
    ok, err = run("G21 G91 G0 X0.5", initial=(10, 0, 0), steps=(1, 1, 1))
    assert ok["final_position_mm"]["x"] == "11"
    # Absolute coordinates round the same way, including negative halves.
    ok, err = run("G21 G90 G0 X-0.5", steps=(1, 1, 1))
    assert ok["final_position_mm"]["x"] == "-1"
    ok, err = run("G21 G90 G0 X-1.5 Y2.5", steps=(1, 1, 1))
    assert ok["final_position_mm"] == {"x": "-2", "y": "3", "z": "0"}
    ok, err = run("G21 G91 G0 X-1.5", initial=(10, 0, 0), steps=(1, 1, 1))
    assert ok["final_position_mm"]["x"] == "8"


def test_steps_relative_moves_accumulate_from_quantized_position():
    # Each 0.6 mm displacement rounds to 1 mm; accumulating from the
    # quantized position gives 2, not round(1.2) = 1.
    ok, err = run("G21 G91 G0 X0.6\nG1 X0.6", steps=(1, 1, 1))
    assert err is None
    assert ok["segments"][0]["end"]["x"] == "1"
    assert ok["segments"][1]["start"]["x"] == "1"
    assert ok["final_position_mm"]["x"] == "2"


def test_steps_unmentioned_axes_keep_actual_position():
    ok, err = run("G21 G90 G0 X2.4", initial=(1, 2, 3), steps=(1, 1, 1))
    assert err is None
    assert ok["final_position_mm"] == {"x": "2", "y": "2", "z": "3"}


def test_steps_move_quantized_to_zero_emits_no_segment():
    ok, err = run("G21 G91 G0 X0.3", steps=(1, 1, 1))
    assert err is None
    assert ok["segments"] == []
    assert ok["final_position_mm"] == {"x": "0", "y": "0", "z": "0"}


def test_steps_zero_displacement_still_geometry_checked():
    region = box((-1, -1, -1), (1, 1, 1))
    ok, err = run("G21 G91 G0 X0.3", regions=[region], steps=(1, 1, 1))
    assert ok is None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 1


def test_steps_initial_position_off_grid_rejected():
    ok, err = run("G21 G90 G0 X1", initial=(0.3, 0, 0), steps=("0.5", "1", "1"))
    assert ok is None
    assert err["error"] == "invalid_request"
    ok, err = run("G21 G90 G0 X1", initial=(0, 0, "0.25"),
                  steps=("0.5", "1", "0.5"))
    assert err["error"] == "invalid_request"
    # Negative grid points are fine when exactly on the grid.
    ok, err = run("G21 G90 G0 X1", initial=("-1.5", 0, 0),
                  steps=("0.5", "1", "1"))
    assert err is None


def test_steps_validation_errors():
    bad_payloads = [
        {},                                   # missing axes
        {"x": 1, "y": 1},                     # missing z
        {"x": 0, "y": 1, "z": 1},             # zero step
        {"x": 1, "y": "-0.5", "z": 1},        # negative step
        {"x": "1_0", "y": 1, "z": 1},         # non-canonical decimal
        {"x": True, "y": 1, "z": 1},          # boolean
        None,                                 # not an object
        [1, 1, 1],                            # not an object
    ]
    for bad in bad_payloads:
        payload = {
            "initial_position_mm": {"x": 0, "y": 0, "z": 0},
            "workspace": {"min": {"x": -10, "y": -10, "z": -10},
                          "max": {"x": 10, "y": 10, "z": 10}},
            "forbidden_regions": [],
            "program": "G21 G90 G0 X1",
            "controller_steps_mm": bad,
        }
        ok, err = audit(payload)
        assert ok is None
        assert err["error"] == "invalid_request", bad


def test_steps_quantized_path_contacting_fixture_is_rejected():
    # Ideal endpoint 4.5 clears the box; the quantized endpoint 5 is inside.
    region = box((4.75, -1, -1), (5.25, 1, 1))
    ok, err = run("G21 G90 G0 X4.5 Y0 Z0", regions=[region])
    assert err is None  # legacy ideal-coordinate audit passes
    ok, err = run("G21 G90 G0 X4.5 Y0 Z0", regions=[region], steps=(1, 1, 1))
    assert ok is None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 1
    assert err["forbidden_region"] == 1
    assert "segments" not in err


def test_steps_quantized_path_checked_against_workspace():
    # Ideal 9.6 is inside the workspace; quantized 10 is not.
    ok, err = run("G21 G90 G0 X9.6", ws_max=(9.8, 100, 100))
    assert err is None
    ok, err = run("G21 G90 G0 X9.6", ws_max=(9.8, 100, 100), steps=(1, 1, 1))
    assert ok is None
    assert err["error"] == "outside_workspace"
    assert err["line"] == 1


def test_steps_quantization_can_avoid_fixture():
    # Ideal 5.2 touches the box; the quantized endpoint 5 is clear.
    region = box((5.1, -1, -1), (5.9, 1, 1))
    ok, err = run("G21 G90 G0 X5.2", regions=[region])
    assert err is not None and err["error"] == "forbidden_contact"
    ok, err = run("G21 G90 G0 X5.2", regions=[region], steps=(1, 1, 1))
    assert err is None
    assert ok["final_position_mm"] == {"x": "5", "y": "0", "z": "0"}


def test_steps_first_violation_line_still_reported():
    region = box((4.75, -1, -1), (5.25, 1, 1))
    program = "G21 G90 G0 X1\nG1 X4.5\nG1 X-50"
    ok, err = run(program, regions=[region], steps=(1, 1, 1))
    assert ok is None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 2


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


def test_audit_endpoint_malformed_json(client):
    resp = client.post("/api/toolpaths/audit", data="not json",
                       content_type="application/json")
    assert resp.status_code == 400


def test_audit_endpoint_steps_quantized_success(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": -100, "y": -100, "z": -100},
                      "max": {"x": 100, "y": 100, "z": 100}},
        "forbidden_regions": [],
        "controller_steps_mm": {"x": "0.5", "y": "1", "z": "2"},
        "program": "G21 G90 G0 X10.3 Y20.4 Z2.9",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["final_position_mm"] == {"x": "10.5", "y": "20", "z": "2"}


def test_audit_endpoint_off_grid_initial_is_400(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": "0.3", "y": 0, "z": 0},
        "workspace": {"min": {"x": -100, "y": -100, "z": -100},
                      "max": {"x": 100, "y": 100, "z": 100}},
        "forbidden_regions": [],
        "controller_steps_mm": {"x": "0.5", "y": "1", "z": "1"},
        "program": "G21 G90 G0 X1",
    })
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_request"


def test_extreme_exponent_in_payload_is_invalid_request_not_500(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": "1e1001", "y": 0, "z": 0},
        "workspace": {"min": {"x": -10, "y": -10, "z": -10},
                      "max": {"x": 10, "y": 10, "z": 10}},
        "forbidden_regions": [],
        "program": "G21 G90 G0 X1",
    })
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_request"
