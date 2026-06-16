"""Tests for the VM price catalog — all offline (HTTP is mocked)."""

import json

from tellurite import pricing
from tellurite.pricing import PriceCatalog, load_price_catalog, normalize_vm_size

# ---------------------------------------------------------------------------
# Normalization + lookup
# ---------------------------------------------------------------------------


def test_normalize_vm_size_adds_prefix():
    assert normalize_vm_size("E8_v3") == "Standard_E8_v3"
    assert normalize_vm_size("Standard_E8_v3") == "Standard_E8_v3"
    assert normalize_vm_size("") == ""


def test_catalog_vm_hourly_normalizes():
    cat = PriceCatalog("azure", "northeurope", "USD", "fallback", "t", {"Standard_E8_v3": 1.06})
    assert cat.vm_hourly("E8_v3") == 1.06  # normalized
    assert cat.vm_hourly("Standard_E8_v3") == 1.06
    assert cat.vm_hourly("Standard_unknown") is None
    assert cat.vm_hourly(None) is None


# ---------------------------------------------------------------------------
# Azure fetch — parsing, filtering, pagination (mocked HTTP)
# ---------------------------------------------------------------------------


def test_fetch_azure_filters_and_paginates(monkeypatch):
    page1 = {
        "Items": [
            {
                "armSkuName": "Standard_E8_v3",
                "unitPrice": 1.10,
                "productName": "VM ESv3 Series",
                "skuName": "E8 v3",
                "meterName": "E8 v3",
            },
            # Windows meter for same SKU — must be ignored
            {
                "armSkuName": "Standard_E8_v3",
                "unitPrice": 2.00,
                "productName": "VM ESv3 Windows",
                "skuName": "E8 v3",
                "meterName": "E8 v3",
            },
            # Spot — must be ignored
            {
                "armSkuName": "Standard_D8s_v3",
                "unitPrice": 0.10,
                "productName": "VM DSv3",
                "skuName": "D8s v3 Spot",
                "meterName": "D8s v3 Spot",
            },
        ],
        "NextPageLink": "https://prices.azure.com/next",
    }
    page2 = {
        "Items": [
            # cheaper usable meter for E8_v3 → should win
            {
                "armSkuName": "Standard_E8_v3",
                "unitPrice": 1.05,
                "productName": "VM ESv3 Series",
                "skuName": "E8 v3",
                "meterName": "E8 v3",
            },
            {
                "armSkuName": "Standard_D8s_v3",
                "unitPrice": 0.77,
                "productName": "VM DSv3",
                "skuName": "D8s v3",
                "meterName": "D8s v3",
            },
            {"armSkuName": "Standard_X", "unitPrice": 0.0, "productName": "VM"},  # zero → dropped
        ],
        "NextPageLink": None,
    }
    pages = iter([page1, page2])
    monkeypatch.setattr(pricing, "_http_get_json", lambda url, timeout: next(pages))

    prices = pricing.fetch_azure_region_prices("northeurope")
    assert prices == {"Standard_E8_v3": 1.05, "Standard_D8s_v3": 0.77}


# ---------------------------------------------------------------------------
# load_price_catalog — fetch success, fallback paths
# ---------------------------------------------------------------------------


def test_load_catalog_uses_live_fetch(monkeypatch):
    monkeypatch.setattr(
        pricing, "fetch_azure_region_prices", lambda region, **kw: {"Standard_E8_v3": 1.0}
    )
    cat = load_price_catalog("azure", "northeurope")
    assert cat.source == "azure-retail"
    assert cat.vm_hourly("Standard_E8_v3") == 1.0


def test_load_catalog_falls_back_on_fetch_error(monkeypatch, tmp_path):
    def boom(region, **kw):
        raise OSError("offline")

    monkeypatch.setattr(pricing, "fetch_azure_region_prices", boom)
    fb = tmp_path / "fb.json"
    fb.write_text(json.dumps({"azure": {"Standard_E8_v3": 9.99}}), encoding="utf-8")
    cat = load_price_catalog("azure", "northeurope", fallback_path=fb)
    assert cat.source == "fallback"
    assert cat.vm_hourly("Standard_E8_v3") == 9.99


def test_load_catalog_offline_uses_fallback(tmp_path):
    fb = tmp_path / "fb.json"
    fb.write_text(json.dumps({"azure": {"Standard_D8s_v3": 0.77}}), encoding="utf-8")
    cat = load_price_catalog("azure", "northeurope", allow_fetch=False, fallback_path=fb)
    assert cat.source == "fallback"
    assert cat.vm_hourly("Standard_D8s_v3") == 0.77


def test_aws_uses_fallback(tmp_path):
    fb = tmp_path / "fb.json"
    fb.write_text(json.dumps({"aws": {"m5.xlarge": 0.19}}), encoding="utf-8")
    cat = load_price_catalog("aws", "eu-west-1", fallback_path=fb)
    assert cat.source == "fallback"
    assert cat.prices == {"m5.xlarge": 0.19}


def test_bundled_fallback_table_loads():
    """The shipped data/vm_prices_fallback.json parses and has Azure entries."""
    cat = load_price_catalog("azure", None, allow_fetch=False)
    assert cat.source == "fallback"
    assert cat.vm_hourly("Standard_E8_v3") is not None


# ---------------------------------------------------------------------------
# Snapshot round-trip (determinism)
# ---------------------------------------------------------------------------


def test_snapshot_round_trip(tmp_path):
    cat = PriceCatalog(
        "azure",
        "northeurope",
        "USD",
        "azure-retail",
        "2026-01-01T00:00:00+00:00",
        {"Standard_E8_v3": 1.05},
    )
    snap = tmp_path / "snap.json"
    cat.to_snapshot(snap)
    restored = PriceCatalog.from_snapshot(snap)
    assert restored.prices == cat.prices
    assert restored.region == "northeurope"
    assert restored.source == "snapshot"
