import pytest

from editor_cli.session.models import (
    EditOperation,
    EditProgram,
    EditRequest,
    EvidenceBinding,
    ReviewReportInput,
    SessionState,
)


def test_session_state_has_closed_loop_order():
    assert [state.value for state in SessionState] == [
        "idle",
        "capture",
        "preserve",
        "analyze",
        "apply",
        "import",
        "preview",
        "verify",
        "correct",
        "ready",
        "blocked",
    ]


def test_edit_request_normalizes_required_operations():
    request = EditRequest(prompt="  remove gaps  ", required_operations=("gaps",))
    assert request.prompt == "remove gaps"
    assert request.required_operations == ("gaps",)


def test_edit_request_rejects_duplicate_or_invalid_required_checks():
    with pytest.raises(ValueError, match="unique"):
        EditRequest("remove gaps", required_operations=("gap_removed", "gap_removed"))
    with pytest.raises(ValueError, match="identifier"):
        EditRequest("remove gaps", required_operations=("Gap removed",))


def test_review_binding_requires_exact_artifact_identity():
    binding = EvidenceBinding(
        session_id="abc123",
        pass_number=1,
        state_version=4,
        project_name="Demo - abc123 - AI Pass 1",
        candidate_sha256="a" * 64,
        preview_sha256="b" * 64,
        manifest_sha256="c" * 64,
        frame_timestamps=(1.0, 2.0),
    )
    report = ReviewReportInput(binding=binding, required={"gap_removed": True})

    assert report.binding == binding


def test_edit_program_rejects_unwrapped_action():
    with pytest.raises(ValueError, match="Unsupported edit action"):
        EditProgram((EditOperation("edit", "run_shell", {}),))


def test_edit_program_rejects_unknown_action_arguments():
    program = EditProgram(
        (EditOperation("edit", "fill_gaps", {"mode": "delete", "path": "/tmp/x"}),)
    )
    with pytest.raises(ValueError, match="unknown arguments"):
        program.validated_for({"duration_seconds": 12.0}, lambda value: value)


def test_edit_program_canonicalizes_each_nested_source_path(tmp_path):
    audio = tmp_path / "sound.wav"
    audio.write_bytes(b"wave")
    template_clip = tmp_path / "clip.mov"
    template_clip.write_bytes(b"movie")
    allowed = {audio.resolve(), template_clip.resolve()}

    program = EditProgram(
        (
            EditOperation("edit", "add_audio", {"src": str(audio), "role": "effects"}),
            EditOperation(
                "generate",
                "apply_template",
                {
                    "template_name": "intro_outro",
                    "clips": {
                        "intro": {
                            "src": str(template_clip),
                            "name": "Intro",
                            "duration": "1s",
                        }
                    },
                },
            ),
        )
    )

    validated = program.validated_for(
        {"duration_seconds": 12.0},
        lambda value: (
            value.resolve()
            if value.resolve() in allowed
            else (_ for _ in ()).throw(PermissionError())
        ),
    )

    assert validated.operations[0].arguments["src"] == str(audio.resolve())
    assert validated.operations[1].arguments["clips"]["intro"]["src"] == str(
        template_clip.resolve()
    )


def test_edit_program_rejects_unknown_nested_template_keys(tmp_path):
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"movie")
    program = EditProgram(
        (
            EditOperation(
                "generate",
                "apply_template",
                {
                    "template_name": "intro_outro",
                    "clips": {
                        "intro": {
                            "src": str(clip),
                            "name": "Intro",
                            "duration": "1s",
                            "neighbor_path": "/tmp/private.mov",
                        }
                    },
                },
            ),
        )
    )

    with pytest.raises(ValueError, match="unknown arguments"):
        program.validated_for({"duration_seconds": 12.0}, lambda value: value.resolve())
