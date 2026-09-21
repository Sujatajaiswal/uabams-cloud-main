from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional

from app.database import db
from app.utils import serialize, utc_now

router = APIRouter()

class RouteCreateRequest(BaseModel):
    name: str
    vertical_limit: float = 50.0
    lateral_limit: float = 80.0

class RouteUpdateRequest(BaseModel):
    name: Optional[str] = None
    vertical_limit: Optional[float] = None
    lateral_limit: Optional[float] = None

class ContactCreateRequest(BaseModel):
    name: str
    mobile_number: str
    designation: Optional[str] = None
    zone: Optional[str] = None
    division: Optional[str] = None
    section: Optional[str] = None
    route_id: int
    sms_enabled: bool = True
    active: bool = True

class ContactUpdateRequest(BaseModel):
    name: Optional[str] = None
    mobile_number: Optional[str] = None
    designation: Optional[str] = None
    zone: Optional[str] = None
    division: Optional[str] = None
    section: Optional[str] = None
    route_id: Optional[int] = None
    sms_enabled: Optional[bool] = None
    active: Optional[bool] = None

@router.get("/api/v1/routes")
async def get_routes():
    routes = await db.pg_pool.fetch("SELECT * FROM routes ORDER BY name ASC")
    return {"routes": serialize([dict(r) for r in routes])}

@router.post("/api/v1/routes")
async def create_route(data: RouteCreateRequest):
    try:
        row = await db.pg_pool.fetchrow(
            "INSERT INTO routes (name, vertical_limit, lateral_limit) VALUES ($1, $2, $3) RETURNING *",
            data.name, data.vertical_limit, data.lateral_limit
        )
        return {"status": "success", "route": serialize(dict(row))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/api/v1/routes/{route_id}")
async def update_route(route_id: int, data: RouteUpdateRequest):
    update_fields = []
    args = [route_id]
    idx = 2
    if data.name is not None:
        update_fields.append(f"name = ${idx}")
        args.append(data.name)
        idx += 1
    if data.vertical_limit is not None:
        update_fields.append(f"vertical_limit = ${idx}")
        args.append(data.vertical_limit)
        idx += 1
    if data.lateral_limit is not None:
        update_fields.append(f"lateral_limit = ${idx}")
        args.append(data.lateral_limit)
        idx += 1
    if not update_fields:
        return {"status": "success"}
        
    query = f"UPDATE routes SET {', '.join(update_fields)} WHERE id = $1 RETURNING *"
    try:
        row = await db.pg_pool.fetchrow(query, *args)
        if not row:
            raise HTTPException(status_code=404, detail="Route not found")
        return {"status": "success", "route": serialize(dict(row))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/v1/routes/{route_id}")
async def delete_route(route_id: int):
    await db.pg_pool.execute("DELETE FROM routes WHERE id = $1", route_id)
    return {"status": "success"}

@router.get("/api/v1/contacts")
async def get_contacts(route_id: Optional[int] = None):
    if route_id:
        contacts = await db.pg_pool.fetch("SELECT * FROM contacts WHERE route_id = $1 ORDER BY name ASC", route_id)
    else:
        contacts = await db.pg_pool.fetch("SELECT * FROM contacts ORDER BY name ASC")
    return {"contacts": serialize([dict(c) for c in contacts])}

@router.post("/api/v1/contacts")
async def create_contact(data: ContactCreateRequest):
    try:
        row = await db.pg_pool.fetchrow(
            "INSERT INTO contacts (name, mobile_number, designation, zone, division, section, route_id, sms_enabled, active) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *",
            data.name, data.mobile_number, data.designation, data.zone, data.division, data.section, data.route_id, data.sms_enabled, data.active
        )
        return {"status": "success", "contact": serialize(dict(row))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/api/v1/contacts/{contact_id}")
async def update_contact(contact_id: int, data: ContactUpdateRequest):
    update_fields = []
    args = [contact_id]
    idx = 2
    if data.name is not None:
        update_fields.append(f"name = ${idx}")
        args.append(data.name)
        idx += 1
    if data.mobile_number is not None:
        update_fields.append(f"mobile_number = ${idx}")
        args.append(data.mobile_number)
        idx += 1
    if data.designation is not None:
        update_fields.append(f"designation = ${idx}")
        args.append(data.designation)
        idx += 1
    if data.zone is not None:
        update_fields.append(f"zone = ${idx}")
        args.append(data.zone)
        idx += 1
    if data.division is not None:
        update_fields.append(f"division = ${idx}")
        args.append(data.division)
        idx += 1
    if data.section is not None:
        update_fields.append(f"section = ${idx}")
        args.append(data.section)
        idx += 1
    if data.sms_enabled is not None:
        update_fields.append(f"sms_enabled = ${idx}")
        args.append(data.sms_enabled)
        idx += 1
    if data.active is not None:
        update_fields.append(f"active = ${idx}")
        args.append(data.active)
        idx += 1
    if data.route_id is not None:
        update_fields.append(f"route_id = ${idx}")
        args.append(data.route_id)
        idx += 1
        
    if not update_fields:
        return {"status": "success"}
        
    query = f"UPDATE contacts SET {', '.join(update_fields)} WHERE id = $1 RETURNING *"
    try:
        row = await db.pg_pool.fetchrow(query, *args)
        if not row:
            raise HTTPException(status_code=404, detail="Contact not found")
        return {"status": "success", "contact": serialize(dict(row))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/v1/contacts/{contact_id}")
async def delete_contact(contact_id: int):
    await db.pg_pool.execute("DELETE FROM contacts WHERE id = $1", contact_id)
    return {"status": "success"}


class AssignRouteRequest(BaseModel):
    route_id: int

@router.put("/api/v1/trains/{train_no}/route")
async def assign_route_to_train(train_no: str, data: AssignRouteRequest):
    """Assign a route to a train so route-wise acceleration limits apply."""
    row = await db.pg_pool.fetchrow(
        "UPDATE trains SET route_id = $1 WHERE train_no = $2 RETURNING train_no, route_id",
        data.route_id, train_no
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Train '{train_no}' not found in the database")
    return {"status": "success", "train_no": row["train_no"], "route_id": row["route_id"]}


@router.get("/api/v1/trains/{train_no}/route")
async def get_train_route(train_no: str):
    """Get the currently assigned route for a train."""
    row = await db.pg_pool.fetchrow(
        """
        SELECT t.train_no, r.id as route_id, r.name as route_name, r.vertical_limit, r.lateral_limit
        FROM trains t
        LEFT JOIN routes r ON t.route_id = r.id
        WHERE t.train_no = $1
        """,
        train_no
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Train '{train_no}' not found")
    return serialize(dict(row))
