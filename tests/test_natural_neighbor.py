import numpy as np
import pytest

pytest.importorskip("metpy")
pytest.importorskip("pyproj")

from tid_analyzer.interpolation.natural_neighbor import (
    METHOD,
    PROJECTION,
    interpolate_prn_epoch_natural_neighbor,
)


class _FakeTransformer:
    @classmethod
    def from_crs(cls, source, target, always_xy=True):
        assert source == "EPSG:4326"
        assert target == "EPSG:3035"
        assert always_xy is True
        return cls()

    def transform(self, lon, lat):
        # Deterministic metre-like projection for unit tests; one degree is
        # large enough that 1e-7 degree near-duplicates round to the same metre.
        return np.asarray(lon, dtype=float) * 1000.0, np.asarray(lat, dtype=float) * 1000.0


def _fake_natural_neighbor_to_grid(point_x, point_y, values, target_x, target_y):
    if np.linalg.matrix_rank(np.column_stack([point_x - point_x[0], point_y - point_y[0]])) < 2:
        raise ValueError("collinear test geometry")
    out = np.full(target_x.shape, np.nan, dtype=float)
    inside = (
        (target_x >= np.min(point_x))
        & (target_x <= np.max(point_x))
        & (target_y >= np.min(point_y))
        & (target_y <= np.max(point_y))
    )
    for index in zip(*np.where(inside), strict=True):
        x = target_x[index]
        y = target_y[index]
        distances = np.hypot(point_x - x, point_y - y)
        if np.min(distances) < 1e-9:
            out[index] = values[np.argmin(distances)]
        else:
            weights = 1.0 / distances**2
            out[index] = np.sum(weights * values) / np.sum(weights)
    return out


@pytest.fixture(autouse=True)
def _patch_projection_and_metpy(monkeypatch):
    import tid_analyzer.interpolation.natural_neighbor as module

    monkeypatch.setattr(module, "Transformer", _FakeTransformer)
    monkeypatch.setattr(module, "natural_neighbor_to_grid", _fake_natural_neighbor_to_grid)


def _base_kwargs(**overrides):
    data = dict(
        prn="G01",
        epoch_index=7,
        time_h=1.25,
        station_codes=["A", "B", "C", "D", "E"],
        ipp_lon=[0.0, 10.0, 20.0, 10.0, 5.0],
        ipp_lat=[40.0, 40.0, 50.0, 60.0, 50.0],
        dtec=[2.0, 2.5, 3.0, 3.5, 2.75],
    )
    data.update(overrides)
    return data


def test_successful_result_has_expected_metadata_dimensions_and_finite_supported_values():
    result = interpolate_prn_epoch_natural_neighbor(**_base_kwargs())

    assert result.status == "ready"
    assert result.method == METHOD
    assert result.projection == PROJECTION
    assert result.grid_step_deg == 0.5
    assert result.lon_values.shape == (141,)
    assert result.lat_values.shape == (121,)
    assert result.values.shape == (121, 141)
    assert result.valid_mask.shape == (121, 141)
    assert np.isfinite(result.values[result.valid_mask]).any()


def test_unsupported_cells_remain_nan_and_invalid():
    result = interpolate_prn_epoch_natural_neighbor(**_base_kwargs())

    assert not result.valid_mask[0, 0]
    assert np.isnan(result.values[0, 0])


def test_exact_duplicate_ipps_are_merged_and_median_dtec_is_used_near_source():
    result = interpolate_prn_epoch_natural_neighbor(
        **_base_kwargs(
            station_codes=["A", "B", "C", "D"],
            ipp_lon=[0.0, 0.0, 10.0, 0.0],
            ipp_lat=[40.0, 40.0, 40.0, 50.0],
            dtec=[10.0, 30.0, 50.0, 70.0],
        )
    )

    assert result.status == "ready"
    assert result.point_count == 3
    lon_index = int(np.where(result.lon_values == 0.0)[0][0])
    lat_index = int(np.where(result.lat_values == 40.0)[0][0])
    assert result.values[lat_index, lon_index] == pytest.approx(20.0, abs=1.0)


def test_near_duplicate_ipps_rounding_to_same_projected_metre_are_merged():
    result = interpolate_prn_epoch_natural_neighbor(
        **_base_kwargs(
            station_codes=["A", "B", "C", "D"],
            ipp_lon=[0.0, 0.0 + 1e-7, 10.0, 0.0],
            ipp_lat=[40.0, 40.0 + 1e-7, 40.0, 50.0],
            dtec=[10.0, 30.0, 50.0, 70.0],
        )
    )

    assert result.status == "ready"
    assert result.point_count == 3


def test_non_finite_and_out_of_bounds_rows_are_rejected_before_counting_points():
    result = interpolate_prn_epoch_natural_neighbor(
        **_base_kwargs(
            station_codes=["A", "B", "C", "BAD1", "BAD2", "BAD3"],
            ipp_lon=[0.0, 10.0, 0.0, np.nan, 60.0, 5.0],
            ipp_lat=[40.0, 40.0, 50.0, 45.0, 45.0, 90.0],
            dtec=[1.0, 2.0, 3.0, 4.0, 5.0, np.inf],
        )
    )

    assert result.status == "ready"
    assert result.point_count == 3
    assert result.station_count == 3


def test_fewer_than_three_unique_points_returns_insufficient_points_nan_grid():
    result = interpolate_prn_epoch_natural_neighbor(
        **_base_kwargs(
            station_codes=["A", "B"],
            ipp_lon=[0.0, 10.0],
            ipp_lat=[40.0, 40.0],
            dtec=[1.0, 2.0],
        )
    )

    assert result.status == "insufficient_points"
    assert result.point_count == 2
    assert not result.valid_mask.any()
    assert np.isnan(result.values).all()


def test_collinear_geometry_is_controlled_geometry_error():
    result = interpolate_prn_epoch_natural_neighbor(
        **_base_kwargs(
            station_codes=["A", "B", "C", "D"],
            ipp_lon=[0.0, 5.0, 10.0, 15.0],
            ipp_lat=[40.0, 40.0, 40.0, 40.0],
            dtec=[1.0, 2.0, 3.0, 4.0],
        )
    )

    assert result.status in {"geometry_error", "ready"}
    if result.status == "geometry_error":
        assert not result.valid_mask.any()
        assert np.isnan(result.values).all()


def test_source_neighbourhood_values_are_reasonable_and_not_clipped():
    result = interpolate_prn_epoch_natural_neighbor(
        **_base_kwargs(
            station_codes=["A", "B", "C", "D"],
            ipp_lon=[0.0, 10.0, 20.0, 0.0],
            ipp_lat=[40.0, 40.0, 50.0, 50.0],
            dtec=[2.5, 3.5, -2.0, 4.0],
        )
    )

    assert result.status == "ready"
    assert np.nanmax(result.values) > 1.0
    lon_index = int(np.where(result.lon_values == 0.0)[0][0])
    lat_index = int(np.where(result.lat_values == 40.0)[0][0])
    assert result.values[lat_index, lon_index] == pytest.approx(2.5, abs=1.0)


def test_rows_input_is_supported():
    rows = [
        {"station_code": "A", "ipp_lon": 0.0, "ipp_lat": 40.0, "dtec": 1.0},
        {"station_code": "B", "ipp_lon": 10.0, "ipp_lat": 40.0, "dtec": 2.0},
        {"station_code": "C", "ipp_lon": 0.0, "ipp_lat": 50.0, "dtec": 3.0},
    ]
    result = interpolate_prn_epoch_natural_neighbor(prn="G02", epoch_index=0, time_h=0.0, rows=rows)

    assert result.status == "ready"
    assert result.point_count == 3
