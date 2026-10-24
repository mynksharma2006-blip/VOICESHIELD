def classify(score: float, inconclusive_margin: float) -> str:
    if score > inconclusive_margin:
        return "likely genuine"
    if score < -inconclusive_margin:
        return "suspicious"
    return "inconclusive"


def classify_screening_score(
    score: float,
    inconclusive_margin: float,
    labels_validated: bool,
) -> str:
    if not labels_validated:
        return "inconclusive"
    return classify(score, inconclusive_margin)


def explain_screening_score(
    score: float | None,
    label: str,
    inconclusive_margin: float,
    labels_validated: bool,
) -> str:
    if score is None:
        return "No voice score is available because the clip could not be scored."

    score_direction = (
        "This lower score leans toward spoof-like audio patterns."
        if score < 0
        else "This higher score leans toward genuine-speech patterns."
        if score > 0
        else "This score is at the model's zero decision point."
    )
    if not labels_validated:
        return (
            f"The model returned a raw score of {score:.4f}. {score_direction} "
            "But the model and threshold have not been validated on representative recordings like this one, "
            "so the result is Can’t tell—not a determination that the voice is fake or genuine."
        )

    threshold_note = (
        f"The score is below the suspicious cutoff of {-inconclusive_margin:.2f}."
        if label == "suspicious"
        else f"The score is above the likely-genuine cutoff of {inconclusive_margin:.2f}."
        if label == "likely genuine"
        else (
            f"The score is between the decision cutoffs of {-inconclusive_margin:.2f} "
            f"and {inconclusive_margin:.2f}."
        )
    )
    return (
        f"The model returned a raw score of {score:.4f}. {score_direction} {threshold_note} "
        "This is a screening signal, not proof of who is speaking."
    )
