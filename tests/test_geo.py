from stations.geo import normalize, place_keys


def test_normalize_handles_saint_dots_and_spacing():
    assert normalize("  St. Louis ") == "st louis"
    assert normalize("Saint  Louis") == "st louis"
    assert normalize("Mc Calla") == "mccalla"
    assert normalize("Ft. Worth") == "fort worth"
    assert normalize("Coeur d'Alene") == "coeur dalene"


def test_place_keys_strip_census_suffixes_once():
    assert "oklahoma city" in place_keys("Oklahoma City city")
    assert "st louis" in place_keys("St. Louis city")
    assert "nashville" in place_keys("Nashville-Davidson metropolitan government (balance)")
    assert "big cabin" in place_keys("Big Cabin town")
    assert "lexington" in place_keys("Lexington-Fayette urban county")
    assert "macon" in place_keys("Macon-Bibb County")
    assert "butte" in place_keys("Butte-Silver Bow (balance)")
    assert "winston" not in place_keys("Winston-Salem city")
