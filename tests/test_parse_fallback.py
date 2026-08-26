from studio_mind.pipeline.parse import fallback_parse

TITLES = [(1, "Nightfall Division"), (2, "The Cartographer's Wife"), (3, "Iron Vestibule")]


def test_title_binding():
    intent = fallback_parse("Why did Nightfall Division lose viewers by episode 5?", TITLES)
    assert intent["title"] == "Nightfall Division"
    assert intent["title_id"] == 1
    assert intent["analysis_type"] == "episode_funnel"


def test_region_and_genre_segment():
    intent = fallback_parse("Which genres retain viewers best in EMEA?", TITLES)
    assert intent.get("region") == "EMEA"
    assert intent["analysis_type"] in ("segment", "retention")


def test_churn_channel_binding():
    intent = fallback_parse(
        "Do partnership-acquired users churn faster than organic users?", TITLES)
    assert intent["analysis_type"] == "churn"
    assert intent.get("acquisition_channel") == "partnership"


def test_defaults_full_window():
    intent = fallback_parse("How is the platform doing?", TITLES)
    assert intent["time_start"].startswith("2026-")
    assert intent["analysis_type"] == "summary"
