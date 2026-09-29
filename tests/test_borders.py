import json

import numpy as np
import pytest

from planner.borders import country_codes, load_borders


@pytest.fixture
def borders(tmp_path):
    # Two boxes with a gap between them (like a river), US south, CA north.
    def box(x0, y0, x1, y1):
        return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}

    path = tmp_path / "borders.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"country": "US"}, "geometry": box(-90, 30, -70, 42.0)},
                    {"type": "Feature", "properties": {"country": "CA"}, "geometry": box(-90, 42.1, -70, 50)},
                ],
            }
        )
    )
    return load_borders(path)


def test_points_get_their_country(borders):
    codes = country_codes(np.array([-80.0, -80.0]), np.array([40.0, 45.0]), borders)
    assert list(codes) == ["US", "CA"]


def test_point_in_neither_polygon_is_unknown(borders):
    assert list(country_codes(np.array([-80.0]), np.array([42.05]), borders)) == [""]


def test_empty_input(borders):
    assert country_codes(np.array([]), np.array([]), borders).size == 0


def test_committed_file_knows_detroit_and_windsor():
    codes = country_codes(np.array([-83.0458, -83.0364]), np.array([42.3314, 42.3149]))
    assert list(codes) == ["US", "CA"]
