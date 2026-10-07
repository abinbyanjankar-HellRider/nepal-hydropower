from datetime import date

from src.processors import cleaners as c


def test_bs_to_ad_known_date():
    # Khimti I commissioning as listed by DoED (BS) -> July 2000
    assert c.bs_to_ad("2057-03-27") == date(2000, 7, 11)


def test_bs_placeholder_and_garbage_are_none():
    assert c.bs_to_ad("0000-00-00") is None
    assert c.bs_to_ad(None) is None
    assert c.bs_to_ad("not a date") is None


def test_dms_conversion_and_placeholder():
    assert c.dms_to_decimal("27o 28' 28\"") == round(27 + 28 / 60 + 28 / 3600, 5)
    assert c.dms_to_decimal("00o 00' 00\"") is None
    assert c.dms_to_decimal(None) is None


def test_resolve_district_variants():
    assert c.resolve_district("Sahure,Hawa (Dolakha) Betali (Ramechhap)") == "Dolakha"
    assert c.resolve_district("Dolakha District") == "Dolakha"
    assert c.resolve_district("(Makawanpur)") == "Makwanpur"
    assert c.resolve_district("Narchyang (Myagdi)") == "Myagdi"
    assert c.resolve_district("nowhere in particular") is None
    assert c.province_of("Dolakha") == "Bagmati"


def test_name_folding_treats_spelling_variants_as_equal():
    same = [("Trisuli", "Trishuli"), ("Budi Gandaki Kha", "Budhi Gandaki Kha"),
            ("Rasuwagadi Hydropower Project", "Rasuwagadhi"), ("Tattopani Khola HEP", "Tatopani khola HEP"),
            ("Khimti -I", "Khimti I"), ("Madhya Marsyangdi", "Middle Marsyangdi"), ("Setikhola HEP", "Seti Khola HEP"), ("Mai Khola", "Maikhola")]
    for a, b in same:
        assert c.normalize_project_name(a) == c.normalize_project_name(b), (a, b)


def test_discriminators_keep_distinct_projects_apart():
    key = lambda n: c.normalize_project_name(n)
    assert key("Upper Tamor") != key("Middle Tamor")
    assert key("Kaligandaki A") != key("Kaligandaki B")
    assert c.discriminators(c.name_tokens("Upper Tamor")) != c.discriminators(c.name_tokens("Lower Tamor"))


def test_company_normalisation():
    assert c.normalize_company_name("NEA") == c.NEA_NAME
    assert c.normalize_company_name("Chilime Hydropower Company Limited (CHPCL) and NEA") == \
        "Chilime Hydropower Company Limited"
    assert c.company_key("Himal Power Ltd.") == c.company_key("Himal Power Limited")


def test_company_names_containing_and_or_ampersand_are_not_cut_in_half():
    """Regression (QA I4): 19 promoters were truncated, e.g. Upper Trishuli-1's developer became 'Nepal Water'."""
    for full in ("Nepal Water & Energy Development Co. P. Ltd", "Research & Development Group",
                 "Himal Hydro and General Construction Ltd.", "KCs Hotel and Multiple Industries P Ltd",
                 "Swet Ganga Hydropower & Construction Limited", "Apex Hydro and Investment Pvt. Ltd."):
        assert c.normalize_company_name(full) == full, full


def test_company_lists_still_keep_only_the_first_company_and_drop_address_text():
    assert c.normalize_company_name("Himal Power Ltd. and Sanima Hydro Ltd") == "Himal Power Ltd."
    assert c.normalize_company_name("Alpha Pvt. Ltd; Beta Pvt. Ltd") == "Alpha Pvt. Ltd"
    assert c.normalize_company_name("United Modi Hydropower Pvt. Ltd., 1st Floor Heritage Plaza 2; Kamaladi") == \
        "United Modi Hydropower Pvt. Ltd."
    assert c.normalize_company_name("Electrocom and Research Centre, 9851003846") == "Electrocom and Research Centre"


def test_series_letters_ka_and_kha_stay_distinct_but_spelling_variants_still_fold():
    """Regression (QA I3): 'Budhi Gandaki Ka' and 'Kha' folded to the same name; '11' folded to '1'."""
    assert c.name_tokens("Budhi Gandaki Ka") != c.name_tokens("Budhi Gandaki Kha")
    assert c.name_tokens("Budhi Gandaki Ga") != c.name_tokens("Budhi Gandaki Gha")
    assert c.name_tokens("Khimti 11") != c.name_tokens("Khimti 1")
    assert c.name_tokens("Project 100 MW") != c.name_tokens("Project 10 MW")
    assert c.name_tokens("Trisuli") == c.name_tokens("Trishuli")                     # existing behaviour kept
    assert c.name_tokens("Budi Gandaki") == c.name_tokens("Budhi Gandaki")
    assert {"kha"} <= set(c.discriminators(c.name_tokens("Budhi Gandaki Kha")))


def test_parse_helpers():
    assert c.parse_capacity("1,234.5 MW") == 1234.5
    assert c.parse_capacity(None) is None
    assert c.parse_year("2023 AD") == 2023
    assert c.parse_year("n/a") is None
