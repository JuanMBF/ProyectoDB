"""Raw zone in MongoDB: every API response is archived exactly as it arrived.

Why keep the raw JSON besides the relational tables:
- The parsers can be fixed or extended later and re-run over history (`reprocess`), without hitting
  the supermarkets again and without losing the days already captured.
- Some data does not fit the relational model and is only useful now and then, like Coto's price in
  each of its branches: it stays in the documents and is queried with an aggregation pipeline.

Writes use `w="majority"`: on the replica set a page is confirmed once most nodes have it, so a
primary failover does not lose acknowledged data.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Protocol

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument, WriteConcern
from pymongo.collection import Collection

from preciosdb.scrapers.base import Page

COLLECTION = "raw_pages"


class RawStore(Protocol):
    def save_page(self, run_id: int, page: Page, fetched_at: datetime) -> str | None: ...

    def pages_for_run(self, run_id: int) -> Iterator[dict[str, Any]]: ...


class NullRawStore:
    """Used when MONGO_URL is not set: nothing is archived and there is nothing to reprocess."""

    enabled = False

    def save_page(self, run_id: int, page: Page, fetched_at: datetime) -> str | None:
        return None

    def pages_for_run(self, run_id: int) -> Iterator[dict[str, Any]]:
        return iter(())


class MongoRawStore:
    enabled = True

    def __init__(self, client: MongoClient[dict[str, Any]], database: str) -> None:
        self.client = client
        self.collection: Collection[dict[str, Any]] = client[database].get_collection(
            COLLECTION, write_concern=WriteConcern(w="majority")
        )

    @classmethod
    def from_url(cls, url: str, database: str) -> MongoRawStore:
        return cls(MongoClient(url, tz_aware=True, serverSelectionTimeoutMS=30_000), database)

    def ensure_indexes(self) -> None:
        # One document per page of a run: re-saving the same page replaces it instead of duplicating it.
        self.collection.create_index(
            [("run_id", ASCENDING), ("term", ASCENDING), ("page", ASCENDING)],
            unique=True,
            name="run_term_page",
        )
        self.collection.create_index([("supermarket", ASCENDING), ("fetched_at", DESCENDING)])

    def save_page(self, run_id: int, page: Page, fetched_at: datetime) -> str | None:
        document = {
            "run_id": run_id,
            "supermarket": page.supermarket,
            "term": page.term,
            "page": page.page,
            "url": page.url,
            "fetched_at": fetched_at,
            "total": page.total,
            "products": len(page.products),
            "payload": page.payload,
        }
        saved = self.collection.find_one_and_replace(
            {"run_id": run_id, "term": page.term, "page": page.page},
            document,
            projection={"_id": 1},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return str(saved["_id"])

    def pages_for_run(self, run_id: int) -> Iterator[dict[str, Any]]:
        for document in self.collection.find({"run_id": run_id}).sort(
            [("term", ASCENDING), ("page", ASCENDING)]
        ):
            fetched_at = document["fetched_at"]
            if fetched_at.tzinfo is None:  # clients created without tz_aware=True
                document["fetched_at"] = fetched_at.replace(tzinfo=UTC)
            yield document

    def stats(self) -> list[dict[str, Any]]:
        """Documents, products and last capture per supermarket (aggregation pipeline)."""
        pipeline: list[dict[str, Any]] = [
            {
                "$group": {
                    "_id": "$supermarket",
                    "pages": {"$sum": 1},
                    "products": {"$sum": "$products"},
                    "runs": {"$addToSet": "$run_id"},
                    "last_fetch": {"$max": "$fetched_at"},
                }
            },
            {"$sort": {"_id": ASCENDING}},
        ]
        return [
            {
                "supermarket": row["_id"],
                "pages": row["pages"],
                "products": row["products"],
                "runs": len(row["runs"]),
                "last_fetch": row["last_fetch"],
            }
            for row in self.collection.aggregate(pipeline)
        ]

    def coto_branch_prices(self, ean: str) -> list[dict[str, Any]]:
        """Price of a product in each Coto branch, from the most recent capture that includes it.

        Coto publishes one price per branch (`data.price[]`); this data only lives in the raw zone.
        """
        codes: list[Any] = [ean]
        if ean.isdigit():
            codes.append(int(ean))  # Coto sends the EAN as a number
        pipeline: list[dict[str, Any]] = [
            {"$match": {"supermarket": "coto"}},
            {"$sort": {"fetched_at": DESCENDING}},
            {"$unwind": "$payload.response.results"},
            {"$project": {"fetched_at": 1, "data": "$payload.response.results.data"}},
            {"$match": {"data.product_main_ean": {"$in": codes}}},
            {"$limit": 1},
            {"$unwind": "$data.price"},
            {
                "$project": {
                    "_id": 0,
                    "fetched_at": 1,
                    "product": "$data.sku_display_name",
                    "branch": "$data.price.store",
                    "price": "$data.price.listPrice",
                }
            },
            {"$sort": {"price": ASCENDING, "branch": ASCENDING}},
        ]
        return list(self.collection.aggregate(pipeline))


def build_raw_store(url: str, database: str) -> MongoRawStore | NullRawStore:
    if not url:
        return NullRawStore()
    store = MongoRawStore.from_url(url, database)
    store.ensure_indexes()
    return store
