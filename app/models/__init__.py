from app.core.db import Base
from app.models.core import (  # noqa: F401 - imported for Alembic metadata
    AppSetting,
    AuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.models.fleet import (  # noqa: F401 - imported for Alembic metadata
    Attachment,
    Notification,
    Trip,
    TripDriver,
    TripFueling,
    TripNote,
    Vehicle,
    VehicleAssignment,
    VehicleDefect,
    VehicleDocument,
    VehicleReservation,
    VehicleService,
)

__all__ = ["Base"]
