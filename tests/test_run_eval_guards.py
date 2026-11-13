from eval.run_eval import frozen_settings_filled


def test_frozen_settings_blank_or_missing_is_not_ok():
    blank = "## Frozen settings\n- Model: ____\n- Threshold: 0.7\n\n## Reruns\nfine"
    assert frozen_settings_filled(blank) is False
    assert frozen_settings_filled("no such heading") is False


def test_frozen_settings_filled_is_ok_even_with_blanks_elsewhere():
    text = (
        "Labels changed: ____\n\n## Frozen settings\n- Model: m\n"
        "- Threshold: 0.7\n\n## Reruns\nx ____"
    )
    assert frozen_settings_filled(text) is True
