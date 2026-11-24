"""Intake rules: when a supplier or component counts as certain, and the questions asked."""

from supplyguard.contracts import ComponentCandidate, SupplierCandidate
from supplyguard.workflow.intake.rules import (
    clarification_message,
    component_question,
    pick_component,
    pick_supplier,
    supplier_question,
)

ATLAS = SupplierCandidate(supplier_id="S001", name="Atlas Components")
ATLAS_LOGISTIK = SupplierCandidate(supplier_id="S006", name="Atlas Logistik GmbH")
MC17 = ComponentCandidate(component_id="MC-17", description="Motor controller board 48V")


def test_exact_name_wins_even_with_several_candidates() -> None:
    assert pick_supplier("Atlas Components", (ATLAS, ATLAS_LOGISTIK)) == ATLAS


def test_supplier_id_is_accepted() -> None:
    assert pick_supplier("s006", (ATLAS, ATLAS_LOGISTIK)) == ATLAS_LOGISTIK


def test_atlas_alone_is_ambiguous_and_asks() -> None:
    assert pick_supplier("Atlas", (ATLAS, ATLAS_LOGISTIK)) is None
    question = supplier_question("Atlas", (ATLAS, ATLAS_LOGISTIK))
    assert question == (
        "Which supplier do you mean by 'Atlas': "
        "Atlas Components (S001) or Atlas Logistik GmbH (S006)?"
    )


def test_single_partial_match_is_accepted() -> None:
    assert pick_supplier(
        "Cordillera", (SupplierCandidate(supplier_id="S004", name="Cordillera Industrial"),)
    )


def test_unknown_supplier_asks() -> None:
    assert pick_supplier("Nobody", ()) is None
    assert "couldn't find a supplier called 'Nobody'" in supplier_question("Nobody", ())


def test_component_id_variants_are_accepted() -> None:
    for written in ("MC-17", "mc17", "component MC 17"):
        assert pick_component(written, (MC17,)) == MC17


def test_component_description_is_never_accepted() -> None:
    assert pick_component("the motor board", (MC17,)) is None
    assert "Did you mean MC-17 (Motor controller board 48V)?" in component_question(
        "the motor board", (MC17,)
    )


def test_all_questions_in_one_message() -> None:
    message = clarification_message(["Which supplier?", "Which component?"])
    assert (
        message == "Before I investigate, I need to confirm:\n- Which supplier?\n- Which component?"
    )
