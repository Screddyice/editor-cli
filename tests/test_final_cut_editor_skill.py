from pathlib import Path


def test_final_cut_skill_requires_rendered_visual_verification():
    text = Path("skills/final-cut-editor/SKILL.md").read_text()
    required = (
        "editor_session",
        "editor_timeline",
        "editor_media",
        "editor_verify",
        "watch",
        "three passes",
        "original project",
        "final export",
    )
    assert all(term in text for term in required)
    assert "XML change is not proof" in text
