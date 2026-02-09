import datetime
import os
import strawberry
import warnings

from .query import Query
from .mutation import Mutation

# Potlač deprecation warning pro strawberry.scalar() - používáme starý způsob, který stále funguje
warnings.filterwarnings("ignore", category=DeprecationWarning, module="strawberry.scalar")
timedelta = strawberry.scalar(
    datetime.timedelta,
    name="timedelta",
    serialize=lambda v: v.total_seconds() / 60,
    parse_value=lambda v: datetime.timedelta(minutes=v),
)


from .BaseGQLModel import Relation
from .BaseGQLModel import BaseGQLModel
from .UserGQLModel import UserGQLModel
from .GroupGQLModel import GroupGQLModel
from .AssetGQLModel import AssetGQLModel
from .AssetInventoryRecordGQLModel import AssetInventoryRecordGQLModel
from .AssetLoanGQLModel import AssetLoanGQLModel
from .query import WhoAmIType

schema = strawberry.federation.Schema(
    query=Query,
    mutation=Mutation,
    types=(
        UserGQLModel,
        GroupGQLModel,
        BaseGQLModel,
        AssetGQLModel,
        AssetInventoryRecordGQLModel,
        AssetLoanGQLModel,
        WhoAmIType,
    ), 
    scalar_overrides={datetime.timedelta: timedelta._scalar_definition},
    extensions=[],
    schema_directives=[],
    enable_federation_2=True
    
)

from uoishelpers.schema import WhoAmIExtension, PrometheusExtension
schema.extensions.append(WhoAmIExtension)
schema.extensions.append(PrometheusExtension(prefix="GQL_Evolution"))

# RolePermissionSchemaExtension přepisuje userRolesForRBACQuery_loader na GraphQLBatchLoader (gql_ug).
# DemoRBACLoaderExtension musí běžet AŽ PO ní: když je x-demo-user-id / ug nedostupný, přepíše loader
# zpět na _UserRolesForRBACLoader (systemdata), aby mutace nevolaly UG a nepadaly na ConnectionRefused.
from .DemoRBACLoaderExtension import DemoRBACLoaderExtension
if os.getenv("DEMO") != "True":
    from uoishelpers.gqlpermissions.RolePermissionSchemaExtension import RolePermissionSchemaExtension
    schema.extensions.append(RolePermissionSchemaExtension)
schema.extensions.append(DemoRBACLoaderExtension)  # vždy jako poslední: obnoví user + loader pro demo / bez UG
