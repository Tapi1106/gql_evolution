import os
import datetime
from pathlib import Path
from typing import Union, Iterable

from uoishelpers.feeders import ImportModels
from uoishelpers.dataloaders import readJsonFile

from src.DBDefinitions import (
    EventModel,
    EventInvitationModel,
    AssetModel,
    AssetInventoryRecordModel,
    AssetLoanModel,
)

PathInput = Union[str, os.PathLike]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_PATH = PROJECT_ROOT / "systemdata.json"
DEFAULT_BACKUP_PATH = PROJECT_ROOT / "systemdata.backup.json"


def _parse_dates(records: Iterable[dict], field_names: Iterable[str]) -> None:
    """Convert selected string timestamp fields to datetime objects in-place."""
    for record in records:
        if not isinstance(record, dict):
            continue
        for field in field_names:
            value = record.get(field, None)
            if isinstance(value, str):
                try:
                    record[field] = datetime.datetime.fromisoformat(value)
                except ValueError:
                    pass


def _normalize_dataset_shapes(data: dict) -> dict:
    """Ensure legacy keys exist even when JSON uses *_evolution variants."""
    if isinstance(data, dict):
        if "events" not in data and isinstance(data.get("events_evolution"), list):
            data["events"] = list(data["events_evolution"])
        if "event_invitations" not in data and isinstance(
            data.get("event_invitations_evolution"), list
        ):
            data["event_invitations"] = list(data["event_invitations_evolution"])
        
        # Normalizace pro asset tabulky
        if "assets" not in data and isinstance(data.get("assets_evolution"), list):
            data["assets"] = list(data["assets_evolution"])
        if "asset_inventory_records" not in data and isinstance(
            data.get("asset_inventory_records_evolution"), list
        ):
            data["asset_inventory_records"] = list(data["asset_inventory_records_evolution"])
        if "asset_loans" not in data and isinstance(data.get("asset_loans_evolution"), list):
            data["asset_loans"] = list(data["asset_loans_evolution"])

        _parse_dates(
            data.get("asset_inventory_records_evolution", []),
            ("record_date", "created", "lastchange"),
        )
        _parse_dates(
            data.get("asset_loans_evolution", []),
            ("startdate", "enddate", "returned_date", "created", "lastchange"),
        )
    return data


def get_demodata():
    data = readJsonFile(jsonFileName=str(DEFAULT_DATA_PATH))
    return _normalize_dataset_shapes(data)


async def initDB(asyncSessionMaker):

    dbModels = [
        EventModel,
        EventInvitationModel,
        AssetModel,
        AssetInventoryRecordModel,
        AssetLoanModel,
    ]

    isDemo = (
        os.environ.get("DEMODATA", None) in ["True", "true", True]
        or os.environ.get("DEMO", None) in ["True", "true", True]
    )
    if isDemo:
        jsonData = get_demodata()
        await ImportModels(asyncSessionMaker, dbModels, jsonData)



async def backupDB(asyncSessionMaker, filename: PathInput = DEFAULT_BACKUP_PATH):
    import sqlalchemy
    import dataclasses
    import json
    from src.DBDefinitions.BaseModel import IDType

    dbModels = [
        EventModel,
        EventInvitationModel,
        AssetModel,
        AssetInventoryRecordModel,
        AssetLoanModel,
    ]
    data = []
    async with asyncSessionMaker() as session:
        for model in dbModels:
            sqlquery = sqlalchemy.select(model)
            rows = await session.execute(sqlquery)
            # vsechny radky do dict
            rowsdict = {}
            for row in rows:
                asdict = dataclasses.asdict(row[0])
                id = asdict.get("id", None)
                if id is None: continue
                rowsdict[id] = asdict
            # vsechny primarn�� klice do ids
            ids = set(rowsdict.keys())
            todo = set()
            done = set()
            chunk_id = 0
            while len(done) < len(ids):
                for row in rowsdict.values():
                    id = row.get("id", None)
                    if id in done: continue
                    skip_this_id = False
                    for key, value in row.items():
                        if key == "id": continue
                        # if not isinstance(value, IDType): continue
                        if value is None: continue
                        if value not in ids: continue
                        if value not in done:
                            skip_this_id = True
                            break
                    if skip_this_id: continue
                    row["_chunk"] = chunk_id
                    todo.add(id)
                if len(todo) == 0: break
                done = done.union(todo)
                todo = set()
                chunk_id += 1
            data.append({
                model.__tablename__: list(rowsdict.values())
            })
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False, default=str)

    import logging
    logging.getLogger(__name__).info("backup done")
