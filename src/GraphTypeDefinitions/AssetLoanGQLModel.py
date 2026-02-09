import typing
import datetime
import strawberry
import json
from pathlib import Path

from uoishelpers.gqlpermissions import OnlyForAuthentized
from uoishelpers.resolvers import (
    getLoadersFromInfo,
    createInputs2,
    InsertError,
    Insert,
    UpdateError,
    Update,
    DeleteError,
    Delete,
    PageResolver,
    ScalarResolver,
)

from .BaseGQLModel import BaseGQLModel, IDType, Relation
from .context_utils import ensure_user_in_context
from src.GraphTypeDefinitions.permissions import user_has_role
from src.error_codes import format_error_message
from uuid import UUID as ErrorCodeUUID

AssetGQLModel = typing.Annotated["AssetGQLModel", strawberry.lazy(".AssetGQLModel")]
UserGQLModel = typing.Annotated["UserGQLModel", strawberry.lazy(".UserGQLModel")]

# Cache uživatelských dat pro rychlý přístup k emailům a jménům
_USER_CACHE = {}
_USER_EMAIL_CACHE = {}  # Mapování email -> user data
_CACHE_LOADED = False

# Mapování UG ID (z Docker databáze) na systemdata ID
_UG_TO_SYSTEMDATA_ID = {
    '2d9dc5ca-a4a2-11ed-b9df-0242ac120003': 'Oliver.Hortik@world.com',  # Oliver z původních dat
    'ccb397ad-0de7-46e7-bff0-42452f11dd5e': 'Ornela.Kuckova@world.com',  # Ornela z původních dat
    'c4a806b8-8913-4849-87f3-c93c3c3d7e1a': 'Estera.Luckova@world.com',  # Estera - PŮVODNÍ admin ID
}

def _load_user_cache():
    """Načte uživatelská data ze systemdata souborů a vytvoří mapování pro UG ID
    Zkouší: systemdata.rnd.json, systemdata.json
    """
    global _USER_CACHE, _USER_EMAIL_CACHE, _CACHE_LOADED
    
    if _CACHE_LOADED:
        return
    
    import logging
    logger = logging.getLogger(__name__)
    
    # Zkus více souborů v pořadí: rnd (priorita), standard
    for filename in ["systemdata.rnd.json", "systemdata.json"]:
        try:
            data_path = Path(__file__).parent.parent.parent / filename
            if data_path.exists():
                logger.debug(f"Loading user cache from {filename}")
                with open(data_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    users = data.get('users', [])
                    logger.debug(f"Found {len(users)} users in {filename}")
                    for user in users:
                        user_id = user.get('id')
                        email = user.get('email', '')
                        user_data = {
                            'email': email,
                            'name': user.get('name', ''),
                            'surname': user.get('surname', ''),
                            'fullname': f"{user.get('name', '')} {user.get('surname', '')}".strip()
                        }
                        if user_id:
                            _USER_CACHE[user_id] = user_data
                        if email:
                            _USER_EMAIL_CACHE[email.lower()] = user_data
                    
                    # Přidej mapování UG ID na user data podle emailu
                    for ug_id, email in _UG_TO_SYSTEMDATA_ID.items():
                        email_data = _USER_EMAIL_CACHE.get(email.lower())
                        if email_data:
                            _USER_CACHE[ug_id] = email_data
                    
                    # Pokud jsme načetli uživatele, můžeme skončit
                    if _USER_CACHE:
                        logger.debug(f"Loaded {len(_USER_CACHE)} users from {filename}")
                        _CACHE_LOADED = True
                        return
        except Exception as e:
            logger.warning(f"Error loading user cache from {filename}: {e}")
            continue
    
    logger.warning("No systemdata file could be loaded for user cache")
    _CACHE_LOADED = True

# Cache se bude loadovat on-demand v resolverech (ne při importu)


@strawberry.input
class AssetLoanInputFilter:
    id: typing.Optional[IDType] = None
    asset_id: typing.Optional[IDType] = None
    borrower_user_id: typing.Optional[IDType] = None
    startdate: typing.Optional[datetime.datetime] = None
    enddate: typing.Optional[datetime.datetime] = None
    returned_date: typing.Optional[datetime.datetime] = None


@strawberry.federation.type(keys=["id"], description="""Asset loan record representing temporary assignment of an asset to a user.
This entity tracks who borrowed what asset, for how long, and includes notes about the loan.
Used for managing asset circulation, tracking returns, and ensuring accountability.""")
class AssetLoanGQLModel(BaseGQLModel):
    @classmethod
    def getLoader(cls, info: strawberry.types.Info):
        return getLoadersFromInfo(info)["AssetLoanModel"]

    asset_id: typing.Optional[IDType] = strawberry.field(
        name="assetId",
        description="Asset id",
        default=None,
        permission_classes=[OnlyForAuthentized],
        directives=[Relation(to="AssetGQLModel")]
    )
    borrower_user_id: typing.Optional[IDType] = strawberry.field(
        name="borrowerUserId",
        description="Borrower user id",
        default=None,
        permission_classes=[OnlyForAuthentized],
        directives=[Relation(to="UserGQLModel")]
    )
    startdate: typing.Optional[datetime.datetime] = strawberry.field(description="Loan start", default=None, permission_classes=[OnlyForAuthentized])
    enddate: typing.Optional[datetime.datetime] = strawberry.field(description="Loan end (expected)", default=None, permission_classes=[OnlyForAuthentized])
    returned_date: typing.Optional[datetime.datetime] = strawberry.field(description="Actual return date", default=None, permission_classes=[OnlyForAuthentized])
    note: typing.Optional[str] = strawberry.field(description="Note", default=None, permission_classes=[OnlyForAuthentized])

    asset: typing.Optional[AssetGQLModel] = strawberry.field(
        description="Asset",
        permission_classes=[OnlyForAuthentized],
        resolver=ScalarResolver[AssetGQLModel](fkey_field_name="asset_id")
    )
    borrower_user: typing.Optional[UserGQLModel] = strawberry.field(
        description="Borrowing user",
        permission_classes=[OnlyForAuthentized],
        resolver=ScalarResolver[UserGQLModel](fkey_field_name="borrower_user_id")
    )

    @strawberry.field(description="Borrower user email", permission_classes=[OnlyForAuthentized])
    async def borrower_user_email(self, info: strawberry.types.Info) -> typing.Optional[str]:
        """Vrátí email uživatele z UG ID cache"""
        if self.borrower_user_id is None:
            return None
        _load_user_cache()
        user_id_str = str(self.borrower_user_id)
        user_data = _USER_CACHE.get(user_id_str)
        return user_data.get('email') if user_data else None

    @strawberry.field(description="Borrower user full name", permission_classes=[OnlyForAuthentized])
    async def borrower_user_fullname(self, info: strawberry.types.Info) -> typing.Optional[str]:
        """Vrátí celé jméno uživatele z UG ID cache"""
        if self.borrower_user_id is None:
            return None
        _load_user_cache()
        user_data = _USER_CACHE.get(str(self.borrower_user_id))
        return user_data.get('fullname') if user_data else None


@strawberry.type(description="Asset loan queries")
class AssetLoanQuery:
    @strawberry.field(
        description="Get loan by id", permission_classes=[OnlyForAuthentized]
    )
    async def asset_loan_by_id(self, info: strawberry.types.Info, id: IDType) -> typing.Optional["AssetLoanGQLModel"]:
        loader = getLoadersFromInfo(info)["AssetLoanModel"]
        row = await loader.load(id)
        if row is None:
            return None
        user = ensure_user_in_context(info)
        if user is None:
            return None
        # Admin může vidět všechno, běžný uživatel jen své půjčky
        if await user_has_role(user, "administrátor", info) or str(row.borrower_user_id) == str(user.get("id")):
            return AssetLoanGQLModel.from_dataclass(row)
        return None

    @strawberry.field(
        description="Page of loans", permission_classes=[OnlyForAuthentized]
    )
    async def asset_loan_page(
        self,
        info: strawberry.types.Info,
        where: typing.Optional[AssetLoanInputFilter] = None,
        skip: typing.Optional[int] = 0,
        limit: typing.Optional[int] = 10,
        orderby: typing.Optional[str] = None,
    ) -> typing.List["AssetLoanGQLModel"]:
        """
        Admin vidí vše; běžný uživatel jen své půjčky (borrower_user_id).
        """
        user = ensure_user_in_context(info)
        if user is None:
            return []

        uid = str(user.get("id"))
        user_email = user.get("email", "")
        is_admin = await user_has_role(user, "administrátor", info)
        loader = getLoadersFromInfo(info)["AssetLoanModel"]

        # Pro ne-admina: vždy jen své půjčky (i když je where filtr s jiným borrowerUserId)
        if not is_admin:
            # Pokud je where filtr s borrowerUserId, zkontroluj, že je to stejný uživatel
            if where is not None and where.borrower_user_id is not None:
                target_user_id = str(where.borrower_user_id)
                if target_user_id != uid:
                    # Viewer se pokouší vidět cizí půjčky - odmítneme
                    return []
            try:
                # Konvertuj uid string na UUID
                user_uuid = IDType(uid)
                # Použij where filtr, pokud je borrowerUserId stejný jako aktuální uživatel
                if where is not None and where.borrower_user_id is not None:
                    rows = await loader.filter_by(borrower_user_id=user_uuid)
                else:
                    rows = await loader.filter_by(borrower_user_id=user_uuid)
                rows_list = list(rows)
                rows_list = rows_list[skip:skip+limit] if skip or limit else rows_list
                return [AssetLoanGQLModel.from_dataclass(row) for row in rows_list]
            except Exception as e:
                return []
        
        # Admin: může vidět všechno, včetně where filtru
        # Pokud je where filtr s borrowerUserId, použij filter_by
        if where is not None and where.borrower_user_id is not None:
            target_user_id = where.borrower_user_id
            rows = await loader.filter_by(borrower_user_id=target_user_id)
            rows_list = list(rows)
            rows_list = rows_list[skip:skip+limit] if skip or limit else rows_list
            return [AssetLoanGQLModel.from_dataclass(row) for row in rows_list]
        
        # Admin bez where filtru: všechny půjčky přes page
        results = await loader.page(skip=skip, limit=limit, orderby=orderby, where=None)
        return [AssetLoanGQLModel.from_dataclass(row) for row in results]


from uoishelpers.resolvers import InputModelMixin


@strawberry.input
class AssetLoanInsertGQLModel(InputModelMixin):
    getLoader = AssetLoanGQLModel.getLoader
    id: typing.Optional[IDType] = strawberry.field(description="id", default=None)
    asset_id: IDType = strawberry.field(name="assetId", description="Asset id")
    borrower_user_id: typing.Optional[IDType] = strawberry.field(
        name="borrowerUserId",
        description="Borrower user id (defaults to current user)",
        default=None
    )
    startdate: typing.Optional[datetime.datetime] = strawberry.field(description="Loan start", default_factory=datetime.datetime.utcnow)
    enddate: typing.Optional[datetime.datetime] = strawberry.field(description="Loan end (expected)", default=None)
    returned_date: typing.Optional[datetime.datetime] = strawberry.field(description="Actual return date", default=None)
    note: typing.Optional[str] = strawberry.field(description="Note", default=None)

    rbacobject_id: strawberry.Private[IDType] = None
    createdby_id: strawberry.Private[IDType] = None


@strawberry.input
class AssetLoanUpdateGQLModel(InputModelMixin):
    getLoader = AssetLoanGQLModel.getLoader
    id: IDType = strawberry.field(description="id")
    lastchange: datetime.datetime = strawberry.field(description="lastchange")
    enddate: typing.Optional[datetime.datetime] = strawberry.field(description="Loan end (expected)", default=None)
    returned_date: typing.Optional[datetime.datetime] = strawberry.field(description="Actual return date", default=None)
    note: typing.Optional[str] = strawberry.field(description="Note", default=None)

    changedby_id: strawberry.Private[IDType] = None


@strawberry.input
class AssetLoanDeleteGQLModel:
    id: IDType = strawberry.field(description="id")
    lastchange: datetime.datetime = strawberry.field(description="lastchange")


@strawberry.type(description="Asset loan mutations")
class AssetLoanMutation:
    @strawberry.field(name="assetLoanInsert", description="Insert loan", permission_classes=[OnlyForAuthentized])
    async def asset_loan_insert(self, info: strawberry.types.Info, loan: AssetLoanInsertGQLModel) -> typing.Union[AssetLoanGQLModel, InsertError[AssetLoanGQLModel]]:
        user = ensure_user_in_context(info)
        if user is None:
            error_code = ErrorCodeUUID("1a0b1c2d-3e4f-4a5b-6c7d-8e9f0a1b2c3d")
            return InsertError[AssetLoanGQLModel](
                msg=format_error_message(error_code),
                code=error_code,
                _entity=None,
                _input=loan
            )
        has_admin_role = await user_has_role(user, "administrátor", info)
        if not has_admin_role:
            error_code = ErrorCodeUUID("3f7a1b2c-4e5d-4a6b-8c9d-0e1f2a3b4c5d")
            return InsertError[AssetLoanGQLModel](
                msg="K této akci nemáte dostatečná oprávnění.",
                code=error_code,
                _entity=None,
                _input=loan
            )
        # Ověř, že asset existuje, jinak vrať srozumitelnou chybu místo FK violation
        asset_loader = getLoadersFromInfo(info)["AssetModel"]
        asset_row = await asset_loader.load(loan.asset_id)
        if asset_row is None:
            error_code = ErrorCodeUUID("4a8b2c3d-5e6f-4b7c-9d0e-1f2a3b4c5d71")
            return InsertError[AssetLoanGQLModel](
                msg=format_error_message(error_code, f"assetId={loan.asset_id}"),
                code=error_code,
                _entity=None,
                _input=loan
            )
        # Vyplň createdby_id a rbacobject_id (Insert / DB je může očekávat)
        loan.createdby_id = IDType(str(user.get("id")))
        loan.rbacobject_id = IDType("d75d64a4-bf5f-43c5-9c14-8fda7aff6c09")
        return await Insert[AssetLoanGQLModel].DoItSafeWay(info=info, entity=loan)

    @strawberry.field(description="Update loan", permission_classes=[OnlyForAuthentized])
    async def asset_loan_update(self, info: strawberry.types.Info, loan: AssetLoanUpdateGQLModel) -> typing.Union[AssetLoanGQLModel, UpdateError[AssetLoanGQLModel]]:
        user = ensure_user_in_context(info)
        if user is None:
            error_code = ErrorCodeUUID("1a0b1c2d-3e4f-4a5b-6c7d-8e9f0a1b2c3d")
            return UpdateError[AssetLoanGQLModel](
                msg=format_error_message(error_code),
                code=error_code,
                _entity=None,
                _input=loan
            )
        

        if not await user_has_role(user, "administrátor", info):
            error_code = ErrorCodeUUID("3f7a1b2c-4e5d-4a6b-8c9d-0e1f2a3b4c5e")
            return UpdateError[AssetLoanGQLModel](
                msg=format_error_message(error_code),
                code=error_code,
                _entity=None,
                _input=loan
            )
        
        result = await Update[AssetLoanGQLModel].DoItSafeWay(info=info, entity=loan)
        return result

    @strawberry.field(description="Delete loan", permission_classes=[OnlyForAuthentized])
    async def asset_loan_delete(self, info: strawberry.types.Info, loan: AssetLoanDeleteGQLModel) -> typing.Optional[DeleteError[AssetLoanGQLModel]]:
        user = ensure_user_in_context(info)
        if user is None:
            error_code = ErrorCodeUUID("1a0b1c2d-3e4f-4a5b-6c7d-8e9f0a1b2c3d")
            return DeleteError[AssetLoanGQLModel](
                msg=format_error_message(error_code),
                code=error_code,
                _entity=None,
                _input=loan
            )
        

        if not await user_has_role(user, "administrátor", info):
            error_code = ErrorCodeUUID("3f7a1b2c-4e5d-4a6b-8c9d-0e1f2a3b4c5f")
            return DeleteError[AssetLoanGQLModel](
                msg=format_error_message(error_code),
                code=error_code,
                _entity=None,
                _input=loan
            )
        
        return await Delete[AssetLoanGQLModel].DoItSafeWay(info=info, entity=loan)
