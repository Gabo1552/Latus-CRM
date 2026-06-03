"""Tests for catalog (Phase 3)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_catalog_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")

from test_simulate_inbound import _FakeDB, _run  # type: ignore


@pytest.fixture
def srv(monkeypatch):
    for mod in list(sys.modules):
        if mod == "server" or mod.startswith(("whatsapp", "utils", "ai", "catalog")):
            sys.modules.pop(mod, None)
    import server  # type: ignore
    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)
    for role, token in (("admin", "T-ADMIN"), ("supervisor", "T-SUP"),
                         ("agent", "T-AGENT"), ("viewer", "T-VIEW")):
        _run(fake.users.insert_one({
            "user_id": f"u_{role}", "email": f"{role}@latus.test", "name": role.title(),
            "role": role, "active": True, "auth_provider": "google",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))
        _run(fake.user_sessions.insert_one({
            "user_id": f"u_{role}", "session_token": token,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))
    return server, fake, TestClient(server.app)


def _h(token): return {"Authorization": f"Bearer {token}"}


# ============================================================================
# CRUD / validation
# ============================================================================


class TestCatalogCRUD:
    def test_01_create_ok(self, srv):
        _, _, c = srv
        r = c.post("/api/catalog/products", headers=_h("T-ADMIN"), json={
            "name": "Zapatilla Latus Runner", "sku": "ZL-001",
            "category": "Indumentaria", "price": 89999,
            "currency": "ARS", "stock_status": "disponible",
            "tags": ["zapatilla", "running"],
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert "_id" not in d
        assert d["product_id"].startswith("prod_")
        assert d["name"] == "Zapatilla Latus Runner"
        assert d["tags"] == ["zapatilla", "running"]
        assert d["active"] is True

    def test_02_create_missing_name_400(self, srv):
        _, _, c = srv
        r = c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": "  "})
        assert r.status_code == 400
        assert "nombre" in r.text.lower()

    def test_03_invalid_currency_400(self, srv):
        _, _, c = srv
        r = c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": "X", "currency": "JPY"})
        assert r.status_code == 400
        assert "Moneda" in r.text

    def test_04_invalid_external_link_400(self, srv):
        _, _, c = srv
        r = c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": "X", "external_link": "not-a-url"})
        assert r.status_code == 400
        assert "enlace" in r.text.lower()

    def test_05_duplicate_sku_409(self, srv):
        _, _, c = srv
        c.post("/api/catalog/products", headers=_h("T-ADMIN"),
               json={"name": "A", "sku": "ABC123"})
        r = c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": "B", "sku": "ABC123"})
        assert r.status_code == 409
        assert "SKU" in r.text

    def test_06_search_q(self, srv):
        _, _, c = srv
        c.post("/api/catalog/products", headers=_h("T-ADMIN"),
               json={"name": "Zapato cuero", "category": "Calzado",
                     "tags": ["cuero"]})
        c.post("/api/catalog/products", headers=_h("T-ADMIN"),
               json={"name": "Mochila urbana", "category": "Accesorios"})
        r = c.get("/api/catalog/products?q=zapato", headers=_h("T-AGENT"))
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 1
        assert "Zapato" in d["items"][0]["name"]

    def test_07_filter_category(self, srv):
        _, _, c = srv
        c.post("/api/catalog/products", headers=_h("T-ADMIN"),
               json={"name": "Remera", "category": "Indumentaria"})
        c.post("/api/catalog/products", headers=_h("T-ADMIN"),
               json={"name": "Mate", "category": "Hogar"})
        r = c.get("/api/catalog/products?category=Indumentaria", headers=_h("T-AGENT"))
        assert r.json()["total"] == 1

    def test_08_sort_price_desc(self, srv):
        _, _, c = srv
        for n, p in [("A", 100), ("B", 300), ("C", 200)]:
            c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": n, "price": p})
        r = c.get("/api/catalog/products?sort=-price", headers=_h("T-AGENT"))
        prices = [i["price"] for i in r.json()["items"]]
        assert prices == [300, 200, 100]

    def test_09_update_changes_updated_at(self, srv):
        _, _, c = srv
        r = c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": "Producto X", "price": 100})
        pid = r.json()["product_id"]
        u1 = r.json()["updated_at"]
        r2 = c.put(f"/api/catalog/products/{pid}", headers=_h("T-ADMIN"),
                   json={"price": 250})
        d2 = r2.json()
        assert d2["price"] == 250
        assert d2["updated_at"] >= u1

    def test_10_soft_delete_and_listing(self, srv):
        _, _, c = srv
        r = c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": "BorrameYa", "price": 9})
        pid = r.json()["product_id"]
        c.delete(f"/api/catalog/products/{pid}", headers=_h("T-ADMIN"))
        assert c.get("/api/catalog/products?q=BorrameYa", headers=_h("T-ADMIN")).json()["total"] == 0
        assert c.get("/api/catalog/products?q=BorrameYa&include_inactive=true",
                     headers=_h("T-ADMIN")).json()["total"] == 1

    def test_11_restore(self, srv):
        _, _, c = srv
        pid = c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                     json={"name": "X"}).json()["product_id"]
        c.delete(f"/api/catalog/products/{pid}", headers=_h("T-ADMIN"))
        c.post(f"/api/catalog/products/{pid}/restore", headers=_h("T-ADMIN"))
        assert c.get(f"/api/catalog/products/{pid}", headers=_h("T-ADMIN")).status_code == 200


# ============================================================================
# CSV import / export
# ============================================================================


class TestCSV:
    CSV_OK = (
        "name,sku,category,price,currency,stock_status,tags\n"
        "Producto A,SKU-A,Cat1,100,ARS,disponible,a;b\n"
        "Producto B,SKU-B,Cat1,200,ARS,sin_stock,b;c\n"
        "Producto C,SKU-C,Cat2,300,USD,consultar,\n"
        "Producto D,SKU-D,Cat2,400,USD,disponible,\n"
        "Producto E,SKU-E,,500,ARS,disponible,\n"
    )

    def test_12_import_5_valid(self, srv):
        _, _, c = srv
        r = c.post("/api/catalog/products/import-csv", headers=_h("T-ADMIN"),
                   files={"file": ("p.csv", self.CSV_OK.encode("utf-8"), "text/csv")})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total_rows"] == 5
        assert d["created"] == 5
        assert d["errors"] == []

    def test_13_import_row_without_name(self, srv):
        _, _, c = srv
        csv = "name,sku\n,SKU-X\n"
        r = c.post("/api/catalog/products/import-csv", headers=_h("T-ADMIN"),
                   files={"file": ("p.csv", csv.encode("utf-8"), "text/csv")})
        d = r.json()
        assert d["created"] == 0
        assert len(d["errors"]) == 1
        e = d["errors"][0]
        assert e["row"] == 2
        assert "nombre" in e["message"].lower()

    def test_14_update_existing_true(self, srv):
        _, _, c = srv
        c.post("/api/catalog/products/import-csv", headers=_h("T-ADMIN"),
               files={"file": ("p.csv", self.CSV_OK.encode("utf-8"), "text/csv")})
        csv2 = "name,sku,price\nProducto A V2,SKU-A,999\n"
        r = c.post("/api/catalog/products/import-csv", headers=_h("T-ADMIN"),
                   data={"update_existing": "true"},
                   files={"file": ("p.csv", csv2.encode("utf-8"), "text/csv")})
        d = r.json()
        assert d["updated"] == 1 and d["created"] == 0
        # verify the change
        items = c.get("/api/catalog/products?q=SKU-A", headers=_h("T-ADMIN")).json()["items"]
        assert items[0]["price"] == 999

    def test_15_update_existing_false_skips(self, srv):
        _, _, c = srv
        c.post("/api/catalog/products/import-csv", headers=_h("T-ADMIN"),
               files={"file": ("p.csv", self.CSV_OK.encode("utf-8"), "text/csv")})
        csv2 = "name,sku,price\nNoOverwrite,SKU-A,1\n"
        r = c.post("/api/catalog/products/import-csv", headers=_h("T-ADMIN"),
                   files={"file": ("p.csv", csv2.encode("utf-8"), "text/csv")})
        d = r.json()
        assert d["skipped"] == 1 and d["created"] == 0 and d["updated"] == 0

    def test_16_export_csv(self, srv):
        _, _, c = srv
        c.post("/api/catalog/products", headers=_h("T-ADMIN"),
               json={"name": "Export Me", "sku": "EX-1", "price": 42})
        r = c.get("/api/catalog/products/export-csv", headers=_h("T-ADMIN"))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"].lower()
        body = r.content.decode("utf-8-sig")
        assert "name,sku,category,description" in body.split("\n")[0]
        assert "Export Me" in body

    def test_17_csv_with_bom(self, srv):
        _, _, c = srv
        csv_bom = b"\xef\xbb\xbfname,sku\nProducto BOM,SKU-BOM\n"
        r = c.post("/api/catalog/products/import-csv", headers=_h("T-ADMIN"),
                   files={"file": ("p.csv", csv_bom, "text/csv")})
        assert r.status_code == 200
        assert r.json()["created"] == 1


# ============================================================================
# RBAC + meta endpoints
# ============================================================================


class TestRBACAndMeta:
    def test_18_agent_can_read_not_write(self, srv):
        _, _, c = srv
        c.post("/api/catalog/products", headers=_h("T-ADMIN"),
               json={"name": "X", "category": "Cat"})
        assert c.get("/api/catalog/products", headers=_h("T-AGENT")).status_code == 200
        assert c.post("/api/catalog/products", headers=_h("T-AGENT"),
                      json={"name": "Y"}).status_code == 403
        assert c.put("/api/catalog/products/whatever", headers=_h("T-AGENT"),
                     json={"name": "Y"}).status_code == 403

    def test_19_categories_and_stats(self, srv):
        _, _, c = srv
        for n, cat in [("A", "Cat1"), ("B", "Cat2"), ("C", "Cat1")]:
            c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": n, "category": cat,
                         "stock_status": "sin_stock" if n == "B" else "disponible"})
        cats = c.get("/api/catalog/categories", headers=_h("T-AGENT")).json()
        assert set(cats["categories"]) == {"Cat1", "Cat2"}
        stats = c.get("/api/catalog/stats", headers=_h("T-AGENT")).json()
        assert stats["total"] == 3
        assert stats["active"] == 3
        assert stats["out_of_stock"] == 1
        assert any(b["name"] == "Cat1" and b["count"] == 2 for b in stats["by_category"])

    def test_20_promo_must_be_less_than_price(self, srv):
        _, _, c = srv
        r = c.post("/api/catalog/products", headers=_h("T-ADMIN"),
                   json={"name": "X", "price": 100, "promo_price": 150})
        assert r.status_code == 400
        assert "promo" in r.text.lower()
