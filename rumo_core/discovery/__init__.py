"""Discovery of companies on the public ATS boards (and career pages of feed employers)."""

# ATS sources that discovery may validate (a token that resolves to a board creates a company).
# The conditional connectors are listed only because their verification passed (marco 3b).
DISCOVERY_SOURCES: tuple[str, ...] = (
    "greenhouse", "lever", "ashby", "smartrecruiters", "breezy",
    "recruitee", "personio", "teamtailor", "bamboohr", "pinpoint",
)
