"""Cloud VM price catalog — the VM/infra half of full-TCO costing (SPEC §6).

Fetches a cloud's **public** price catalog (Azure Retail Prices today; AWS falls back
to the bundled table) for a region, then exposes node-type → hourly USD lookups. The
fetch is a pure inbound pull parameterized on **cloud + region only** (Golden Rule #3):
the request carries no workspace/job/cluster identifiers. A bundled fallback table
covers offline/air-gapped runs, and a fetched catalog can be snapshotted to disk so a
re-run reproduces byte-for-byte (Golden Rule #4).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

AZURE_RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
_FALLBACK_PATH = Path(__file__).parent.parent / "data" / "vm_prices_fallback.json"


def normalize_vm_size(vm_size: str) -> str:
    """Normalize a node type to Azure's `armSkuName` form (ensure `Standard_` prefix)."""
    vm_size = (vm_size or "").strip()
    if not vm_size:
        return vm_size
    return vm_size if vm_size.startswith("Standard_") else f"Standard_{vm_size}"


@dataclass(frozen=True)
class PriceCatalog:
    """Immutable node-type → hourly USD price map for one cloud + region."""

    cloud: str
    region: str
    currency: str
    source: str  # "azure-retail" | "aws-pricing" | "fallback" | "snapshot"
    fetched_at: str  # ISO-8601 UTC
    prices: dict[str, float]  # normalized VM size → hourly price

    def vm_hourly(self, node_type: str | None) -> float | None:
        """Hourly price for a node type, or None if unknown (caller must fail soft)."""
        if not node_type:
            return None
        return self.prices.get(normalize_vm_size(node_type))

    # -- snapshotting (determinism) -----------------------------------------
    def to_snapshot(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def from_snapshot(cls, path: Path) -> PriceCatalog:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{**data, "source": "snapshot"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _http_get_json(url: str, timeout: float) -> dict[str, Any]:
    # Cloud + region only in the URL; no workspace data leaves (Golden Rule #3).
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "tellurite-audit"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https public price API)
        return json.loads(resp.read().decode("utf-8"))


def _is_usable_meter(item: dict[str, Any]) -> bool:
    """Keep Linux on-demand consumption meters; drop Windows / Spot / Low Priority."""
    blob = " ".join(str(item.get(k, "")) for k in ("productName", "skuName", "meterName")).lower()
    return not ("windows" in blob or "spot" in blob or "low priority" in blob)


def fetch_azure_region_prices(
    region: str, *, currency: str = "USD", timeout: float = 30.0, max_pages: int = 50
) -> dict[str, float]:
    """Fetch all on-demand Linux VM hourly prices for an Azure region (paginated).

    Returns `armSkuName → hourly price` (cheapest usable meter per SKU). The OData
    filter sends only serviceName + region + priceType — no workspace identifiers.
    """
    flt = (
        f"serviceName eq 'Virtual Machines' "
        f"and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    url: str | None = (
        f"{AZURE_RETAIL_PRICES_URL}?{urlencode({'$filter': flt, 'currencyCode': currency})}"
    )
    prices: dict[str, float] = {}
    pages = 0
    while url and pages < max_pages:
        data = _http_get_json(url, timeout)
        for item in data.get("Items", []):
            sku = item.get("armSkuName")
            price = item.get("unitPrice")
            if not sku or not price or price <= 0 or not _is_usable_meter(item):
                continue
            current = prices.get(sku)
            if current is None or price < current:
                prices[sku] = float(price)
        url = data.get("NextPageLink") or None
        pages += 1
    return prices


def _load_fallback(cloud: str, fallback_path: Path | None = None) -> dict[str, float]:
    path = fallback_path or _FALLBACK_PATH
    try:
        table = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Fallback price table not found at %s", path)
        return {}
    return {k: float(v) for k, v in table.get(cloud.lower(), {}).items()}


def load_price_catalog(
    cloud: str,
    region: str | None,
    *,
    allow_fetch: bool = True,
    currency: str = "USD",
    fallback_path: Path | None = None,
) -> PriceCatalog:
    """Build a PriceCatalog: fetch the live public catalog, else the bundled fallback.

    Fetch is attempted for Azure when `allow_fetch` and a region is given; any failure
    (offline, API error, empty result) falls back to the bundled table. AWS live pricing
    is not implemented yet — it uses the fallback table.
    """
    cloud = cloud.lower()
    if allow_fetch and region and cloud == "azure":
        try:
            prices = fetch_azure_region_prices(region, currency=currency)
            if prices:
                return PriceCatalog(cloud, region, currency, "azure-retail", _now_iso(), prices)
            logger.warning("Azure price fetch returned no rows for %s; using fallback.", region)
        except Exception as exc:  # noqa: BLE001 — fail soft to the bundled table
            logger.warning("Azure price fetch failed (%s); using fallback table.", exc)
    elif allow_fetch and cloud == "aws":
        logger.warning("AWS live pricing not implemented; using bundled fallback table.")

    return PriceCatalog(
        cloud, region or "", currency, "fallback", _now_iso(), _load_fallback(cloud, fallback_path)
    )
