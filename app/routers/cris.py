"""CRIS export REST API endpoints.

Provides endpoints to generate, list, inspect, transfer, and retry
CRIS export files.

NOTE: These endpoints do not currently require operator authentication.
Add auth before going live in production.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.cris.exporter import CrisExporter
from app.cris import repository as repo
from app.utils import is_operator_authenticated


def require_operator_auth(request: Request) -> None:
    """Enforce operator authentication for CRIS management endpoints."""
    if not is_operator_authenticated(request):
        raise HTTPException(status_code=401, detail="Operator authentication required")


router = APIRouter(
    prefix="/api/v1/cris",
    tags=["CRIS Export"],
    dependencies=[Depends(require_operator_auth)],
)


# ── Request / Response models ──────────────────────────────────────────────────

class GenerateExportRequest(BaseModel):
    archiveSha256:     str  = Field(..., description="SHA-256 of the source archive")
    gatewayId:         str  = Field(..., description="Physical gateway ID")
    trainId:           str | None = Field(None, description="Train number")
    sessionName:       str | None = Field(None, description="Session name")
    logicalGatewayId:  str | None = Field(None, description="Logical gateway ID")
    exportType:        str | None = Field(
        None,
        description="One of SPATIAL, PROCESSED, ALERTS. Omit to generate all three."
    )
    forceRegenerate:   bool = Field(False, description="Overwrite existing export file")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _serialize_export(record: dict[str, Any]) -> dict[str, Any]:
    """Convert a cris_exports row to a JSON-safe dict."""
    out: dict[str, Any] = {}
    for k, v in record.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/exports", summary="Generate CRIS export file(s)")
async def generate_exports(body: GenerateExportRequest) -> dict[str, Any]:
    """Generate one or all three CRIS export types for a given archive.

    If *exportType* is provided, only that type is generated.
    Otherwise SPATIAL, PROCESSED, and ALERTS are all generated.
    """
    exporter = CrisExporter()
    export_types = (
        [body.exportType.upper()]
        if body.exportType
        else ["SPATIAL", "PROCESSED", "ALERTS"]
    )

    results: list[dict[str, Any]] = []
    errors:  list[str] = []

    for et in export_types:
        try:
            record = await exporter.generate_export(
                archive_sha256=body.archiveSha256,
                gateway_id=body.gatewayId,
                export_type=et,
                train_id=body.trainId,
                session_name=body.sessionName,
                logical_gateway_id=body.logicalGatewayId,
                force_regenerate=body.forceRegenerate,
            )
            results.append(_serialize_export(record))
        except Exception as exc:
            errors.append(f"{et}: {exc}")

    return {
        "exports": results,
        "errors":  errors,
    }


@router.get("/exports", summary="List CRIS exports")
async def list_exports(
    status:      str | None = Query(None, description="Filter by status"),
    trainId:     str | None = Query(None, description="Filter by train ID"),
    gatewayId:   str | None = Query(None, description="Filter by gateway ID"),
    exportType:  str | None = Query(None, description="Filter by export type"),
    limit:  int = Query(50, ge=1, le=500, description="Page size"),
    offset: int = Query(0,  ge=0, description="Page offset"),
) -> dict[str, Any]:
    """List CRIS export records with optional filters."""
    rows = await repo.list_exports(
        status=status,
        train_id=trainId,
        gateway_id=gatewayId,
        export_type=exportType.upper() if exportType else None,
        limit=limit,
        offset=offset,
    )
    return {"exports": [_serialize_export(r) for r in rows], "count": len(rows)}


@router.get("/exports/{export_id}", summary="Get a single CRIS export")
async def get_export(export_id: int) -> dict[str, Any]:
    """Retrieve the details and status of a single CRIS export record."""
    record = await repo.get_export(export_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Export {export_id} not found")
    return _serialize_export(record)


@router.post("/exports/{export_id}/transfer", summary="Transfer a generated export")
async def transfer_export(export_id: int) -> dict[str, Any]:
    """Transfer an existing generated file to the configured CRIS destination."""
    record = await repo.get_export(export_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Export {export_id} not found")
    if record.get("status") not in ("GENERATED", "FAILED"):
        raise HTTPException(
            status_code=400,
            detail=f"Export {export_id} is in status '{record.get('status')}' "
                   "and cannot be transferred. Must be GENERATED or FAILED."
        )
    try:
        exporter = CrisExporter()
        updated = await exporter.transfer_export(export_id)
        return _serialize_export(updated)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/exports/{export_id}/retry", summary="Retry a failed export or transfer")
async def retry_export(export_id: int) -> dict[str, Any]:
    """Retry a FAILED export: re-generates the file and optionally re-transfers it."""
    record = await repo.get_export(export_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Export {export_id} not found")
    if record.get("status") not in ("FAILED", "PENDING"):
        raise HTTPException(
            status_code=400,
            detail=f"Export {export_id} is in status '{record.get('status')}'. "
                   "Only FAILED or PENDING exports can be retried."
        )
    try:
        exporter = CrisExporter()
        updated = await exporter.generate_export(
            archive_sha256=record["archive_sha256"],
            gateway_id=record["gateway_id"],
            export_type=record["export_type"],
            train_id=record.get("train_id"),
            session_name=record.get("session_name"),
            logical_gateway_id=record.get("logical_gateway_id"),
            force_regenerate=True,
        )
        return _serialize_export(updated)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
