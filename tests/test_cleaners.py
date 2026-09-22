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


def test_parse_helpers():
    assert c.parse_capacity("1,234.5 MW") == 1234.5
    assert c.parse_capacity(None) is None
    assert c.parse_year("2023 AD") == 2023
    assert c.parse_year("n/a") is None
