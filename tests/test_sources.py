"""
Offline tests for source discovery. Each fixture copies the shape a real
catalog had when it broke us (Nov 2026), so a format change that slips
through the parsers fails here instead of mid-download.

    pytest tests/                       # offline, < 1 s
    python tests/check_sources_live.py  # hits the real catalogs (network)
"""
import pytest

from pipeline import sources


class FakeResp:
    def __init__(self, json=None, text=""):
        self._json, self.text, self.status_code, self.ok = json, text, 200, True

    def json(self):
        return self._json

    def raise_for_status(self):
        pass


@pytest.fixture(autouse=True)
def _reset_cache():
    sources._cms_catalog_cache = None
    yield
    sources._cms_catalog_cache = None


def _dist(title, url):
    return [
        {"title": title, "format": "API", "accessURL": "https://data.cms.gov/data-api/x"},
        {"title": title, "mediaType": "text/csv", "format": "CSV", "downloadURL": url},
    ]


CMS_CATALOG = {"dataset": [
    # one entry per release, title '<name> : <date>' (2026 format)
    {"title": "Medicare Part D Prescribers - by Provider and Drug : 2023-12-31",
     "distribution": _dist("Medicare Part D Prescribers - by Provider and Drug : 2023-12-31", "https://x/DY23.csv")},
    {"title": "Medicare Part D Prescribers - by Provider and Drug : 2024-12-01",
     "distribution": _dist("Medicare Part D Prescribers - by Provider and Drug : 2024-12-01", "https://x/DY24.csv")},
    # near-miss names must not match
    {"title": "Medicare Part D Prescribers - by Provider : 2024-12-03",
     "distribution": _dist("x", "https://x/WRONG_by_provider.csv")},
    {"title": "Medicare Quarterly Part D Spending by Drug : 2026-04-01",
     "distribution": _dist("x", "https://x/WRONG_quarterly.csv")},
    {"title": "Medicare Part D Spending by Drug : 2024-01-01",
     "distribution": _dist("Medicare Part D Spending by Drug : 2024-01-01", "https://x/DSD_DY24.csv")},
    {"title": "Medicare Part D Spending by Drug : 2023-01-01",
     "distribution": _dist("Medicare Part D Spending by Drug : 2023-01-01", "https://x/DSD_DY23.csv")},
]}


def test_cms_csv_by_year(monkeypatch):
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: FakeResp(json=CMS_CATALOG))
    name = "Medicare Part D Prescribers - by Provider and Drug"
    assert sources.cms_csv_url(name, 2024) == "https://x/DY24.csv"
    assert sources.cms_csv_url(name, 2023) == "https://x/DY23.csv"
    with pytest.raises(LookupError, match="Available"):
        sources.cms_csv_url(name, 2019)


def test_cms_latest_ignores_quarterly(monkeypatch):
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: FakeResp(json=CMS_CATALOG))
    assert sources.cms_latest_csv_url("Medicare Part D Spending by Drug") == "https://x/DSD_DY24.csv"


def test_cms_old_single_entry_format(monkeypatch):
    old = {"dataset": [{"title": "Medicare Part D Spending by Drug",
                        "distribution": _dist("Medicare Part D Spending by Drug : 2022-01-01", "https://x/old.csv")}]}
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: FakeResp(json=old))
    assert sources.cms_latest_csv_url("Medicare Part D Spending by Drug") == "https://x/old.csv"


NPPES_HTML = """
<li><a id='DDSMTH.ZIP.D' href='./NPPES_Data_Dissemination_November_2026_V2.zip'>full</a></li>
<li><a href='./NPPES_Deactivated_NPI_Report_110926_V2.zip'>deact</a></li>
<li><a href='./NPPES_Data_Dissemination_110226_110826_Weekly_V2.zip'>weekly</a></li>
"""


def test_nppes_single_quoted_v2(monkeypatch):
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: FakeResp(text=NPPES_HTML))
    assert sources.nppes_full_url() == \
        "https://download.cms.gov/nppes/NPPES_Data_Dissemination_November_2026_V2.zip"


def test_nppes_double_quoted(monkeypatch):
    html = '<a href="NPPES_Data_Dissemination_March_2025.zip">x</a>'
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: FakeResp(text=html))
    assert sources.nppes_full_url().endswith("NPPES_Data_Dissemination_March_2025.zip")


def test_places_newest_release_comma_title(monkeypatch):
    res = {"results": [{"resource": {"name": n, "id": i}} for n, i in [
        ("PLACES: Local Data for Better Health, County Data 2024 release", "old1"),
        ("PLACES: Local Data for Better Health, County Data, 2025 release", "new1"),
        ("PLACES: County Data (GIS Friendly Format), 2025 release", "gis1"),
    ]]}
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: FakeResp(json=res))
    assert "/new1/" in sources.cdc_places_county_url()


def test_open_payments_title(monkeypatch):
    items = [
        {"title": "2023 General Payment Data", "distribution": [{"downloadURL": "https://x/2023.csv"}]},
        {"title": "2024 General Payment Data", "distribution": [{"data": {"downloadURL": "https://x/2024.csv"}}]},
        {"title": "2024 Research Payment Data", "distribution": [{"downloadURL": "https://x/research.csv"}]},
    ]
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: FakeResp(json=items))
    assert sources.open_payments_general_url(2024) == "https://x/2024.csv"


def test_registry_shape():
    for name, s in sources.SOURCES.items():
        assert s.name == name
        assert s.raw_table.isidentifier()
        if s.usecols:
            assert len(s.usecols) == len(set(s.usecols)), f"{name}: duplicate usecols"
