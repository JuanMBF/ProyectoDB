from datetime import timedelta

from preciosdb.raw_store import MongoRawStore, NullRawStore, build_raw_store
from preciosdb.samples import load
from preciosdb.scrapers.base import Page
from tests.conftest import DAY1, DAY2


def page(term="leche", number=1, supermarket="coto", payload=None):
    return Page(supermarket, term, number, f"https://example/{term}/{number}", payload or {"response": {}})


def test_saving_the_same_page_twice_replaces_it(raw_store):
    first = raw_store.save_page(1, page(), DAY1)
    second = raw_store.save_page(1, page(payload={"response": {"results": []}}), DAY2)

    assert first == second
    assert raw_store.collection.count_documents({}) == 1
    assert raw_store.collection.find_one()["fetched_at"] == DAY2


def test_pages_for_run_in_order_and_timezone_aware(raw_store):
    raw_store.save_page(1, page("yerba", 1), DAY1)
    raw_store.save_page(1, page("leche", 2), DAY1)
    raw_store.save_page(1, page("leche", 1), DAY1)
    raw_store.save_page(2, page("leche", 1), DAY1)

    documents = list(raw_store.pages_for_run(1))

    assert [(d["term"], d["page"]) for d in documents] == [("leche", 1), ("leche", 2), ("yerba", 1)]
    assert all(d["fetched_at"].utcoffset() == timedelta(0) for d in documents)


def test_naive_dates_from_the_driver_are_treated_as_utc(raw_store):
    raw_store.collection.insert_one(
        {"run_id": 9, "term": "x", "page": 1, "fetched_at": DAY1.replace(tzinfo=None)}
    )
    [document] = raw_store.pages_for_run(9)
    assert document["fetched_at"] == DAY1


def test_coto_branch_prices_reads_the_latest_capture(raw_store):
    raw_store.save_page(1, page(payload=load("day1", "coto")), DAY1)
    raw_store.save_page(2, page(payload=load("day2", "coto")), DAY2)

    rows = raw_store.coto_branch_prices("7790895000997")  # Coca-Cola: 4800 on day 1, 4950 on day 2

    assert len(rows) == 5
    assert {r["fetched_at"] for r in rows} == {DAY2}
    assert [r["price"] for r in rows] == [4785, 4950, 4950, 4950, 5070]
    assert rows[0]["branch"] == "061"
    assert raw_store.coto_branch_prices("0000000000000") == []


def test_stats_per_supermarket(raw_store):
    raw_store.save_page(1, page("leche", payload=load("day1", "coto")), DAY1)
    raw_store.save_page(1, page("yerba", payload=load("day1", "coto")), DAY1)
    raw_store.save_page(2, page("leche", supermarket="dia"), DAY2)

    stats = {s["supermarket"]: s for s in raw_store.stats()}

    assert stats["coto"]["pages"] == 2
    assert stats["coto"]["runs"] == 1
    assert stats["dia"]["last_fetch"] == DAY2


def test_null_store_does_nothing():
    store = NullRawStore()
    assert store.save_page(1, page(), DAY1) is None
    assert list(store.pages_for_run(1)) == []


def test_build_raw_store_without_url():
    assert isinstance(build_raw_store("", "x"), NullRawStore)


def test_majority_write_concern(raw_store: MongoRawStore):
    assert raw_store.collection.write_concern.document == {"w": "majority"}
