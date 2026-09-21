from datetime import datetime
from typing import Literal, Any

from pydantic import BaseModel, Field


class HandshakeRequest(BaseModel):
    gatewayId: str = Field(..., examples=["GW1"])
    trainId: str | None = Field(None, examples=["12345"])
    trainIdDirA: str | None = Field(None)
    logicalGatewayIdDirA: str | None = Field(None)
    trainIdDirB: str | None = Field(None)
    logicalGatewayIdDirB: str | None = Field(None)
    gatewaySerial: str = Field(..., examples=["SN001"])
    firmwareVersion: str = Field(..., examples=["1.0"])
    clientCertPem: str | None = Field(default=None, description="Optional PEM encoded X.509 client certificate for PKI auto-provisioning")
    sshPublicKey: str | None = Field(default=None, description="Optional SSH public key for secure upload")


class UploadLeaseRequest(BaseModel):
    gatewayId: str = Field(..., examples=["GW1_20693_BOGIE_01"])
    logicalGatewayId: str | None = Field(None, examples=["GW1_22151_BOGIE_01"])
    trainId: str = Field(..., examples=["20693"])
    sessionName: str = Field(..., examples=["SESSION_20260727_103000000"])
    zipFileName: str = Field(..., examples=["GW1_20693_BOGIE_01__20693__SESSION_20260727_103000000.zip"])
    sha256: str = Field(..., examples=["0f4c2a"])
    sizeBytes: int = Field(..., examples=[523000000])


class UploadCompleteRequest(BaseModel):
    gatewayId: str | None = Field(None, examples=["GW1_20693_BOGIE_01"])
    logicalGatewayId: str | None = Field(None, examples=["GW1_22151_BOGIE_01"])
    trainId: str = Field(..., examples=["20693"])
    uploadId: str = Field(..., examples=["b8bc2cf7-3d91-4d3d-8321-5e1d46e6f001"])
    sessionName: str | None = Field(None, examples=["SESSION_20260727_103000000"])
    sha256: str = Field(..., examples=["0f4c2a"])
    sizeBytes: int = Field(..., examples=[523000000])


class AuthRequest(BaseModel):
    gatewayId: str = Field(..., examples=["GW1"])
    trainId: str | None = Field(None, examples=["019456"])
    apiKey: str = Field(..., examples=["123456"])
    sessionId: str = Field(..., examples=["sessionId"])


class NodeStatus(BaseModel):
    status: Literal["PENDING", "IN_PROGRESS", "COMMITTED", "FAILED"]
    committedVersion: int
    targetVersion: int
    valuesChanged: bool
    error: str | None = None


class CalibrationNodes(BaseModel):
    adxlLeft: NodeStatus | None = None
    adxlRight: NodeStatus | None = None
    bogie: NodeStatus | None = None
    encoder: NodeStatus | None = None


class CommandResultItem(BaseModel):
    commandId: str
    type: Literal["reset", "calibration_update"]
    status: Literal["success", "failed", "staged", "partial_success", "ignored"]
    completedAt: datetime | None = None
    location: dict | None = None
    details: dict | None = None
    reason: str | None = None
    nodes: CalibrationNodes | None = None

    class Config:
        extra = "ignore"


class HeartbeatRequest(BaseModel):
    gatewayId: str = Field(..., examples=["GW1"])
    logicalGatewayId: str | None = Field(None, examples=["GW1_22151_BOGIE_01"])
    trainId: str | None = Field(None, examples=["22151"])
    gatewaySerial: str | None = Field(None, examples=["UABAMS_PIL_01"])
    timestamp: datetime | None = None
    token: str | None = Field(None, examples=["jwt_token"])
    adxlState: str | None = None
    adxlUptime: int | None = None
    adxlFaults: int | None = None
    adxlFwVersion: str | None = None
    adxlCalVersion: int | None = None
    encoderState: str | None = None
    encoderUptime: int | None = None
    encoderFaults: int | None = None
    encoderFwVersion: str | None = None
    encoderCalVersion: int | None = None
    commandResults: list[CommandResultItem] = Field(default_factory=list)


class ADXLCalibrationValues(BaseModel):
    offset_x: int = 0
    offset_y: int = 0
    offset_z: int = 0


class CalibrationUpdateRequest(BaseModel):
    adxlLeft: ADXLCalibrationValues | None = None
    adxlRight: ADXLCalibrationValues | None = None
    bogie: dict | None = None
    encoder: dict | None = None


class AxisAlertItem(BaseModel):
    sensor: str = Field(..., description="Sensor identity: AXLE_LEFT, AXLE_RIGHT, BOGIE")
    axis: str = Field(..., description="Axis: X, Y, Z")
    channel: str = Field(..., description="Channel: AL_X, AL_Y, AL_Z, AR_X, AR_Y, AR_Z, BG_X, BG_Y, BG_Z")
    peakValueG: float = Field(..., description="Acceleration peak in g, 4 decimal places")
    thresholdG: float = Field(..., description="Configured threshold in g, 4 decimal places")
    speedKmph: float = Field(..., description="Train speed at the exact moment of this axis peak, 2 decimal places")
    locationKm: float = Field(..., description="Chainage from start in km, 5 decimal places = 1 cm accuracy")
    latitude: float = Field(..., description="GPS Latitude of peak, 6 decimal places")
    longitude: float = Field(..., description="GPS Longitude of peak, 6 decimal places")


class AlertRequest(BaseModel):
    gatewayId: str = Field(..., description="Physical Gateway ID, e.g. GW1_PIL_BOGIE_01")
    logicalGatewayId: str | None = Field(None, description="Directional Gateway ID, e.g. GW1_22151_BOGIE_01")
    trainNo: str = Field(..., description="Train Number, e.g. 22151")
    sessionName: str = Field(..., description="Active Session Name, e.g. 20260911_111245_22151_UP")
    timestampUtcMs: int = Field(..., description="Unix timestamp in ms")
    startKm: float = Field(..., description="Window start in km, 5 decimal places for 1 cm accuracy")
    endKm: float = Field(..., description="Window end in km, 5 decimal places for 1 cm accuracy")
    speedKmph: float = Field(..., description="Window average speed in km/h, 2 decimal places")
    minSpeedKmph: float = Field(..., description="Window min speed in km/h, 2 decimal places")
    maxSpeedKmph: float = Field(..., description="Window max speed in km/h, 2 decimal places")
    alertsCount: int = Field(..., description="Total count of exceeded axes in this window")
    alerts: list[AxisAlertItem] = Field(default_factory=list, description="Array of exceeded axes")


class ResetSessionRequest(BaseModel):
    trainNo: str
    adminPassword: str | None = None


class TargetedResetRequest(BaseModel):
    trainNo: str
    gatewayId: str | None = None
    startTime: datetime | None = None
    endTime: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    radiusMeters: float = 100.0
    reason: str | None = None


class ActivityLogRequest(BaseModel):
    page: str
    action: str = "page_view"
    message: str | None = None
    errorMessage: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class GatewayStatus(BaseModel):
    gatewayId: str
    online: bool
    lastHeartbeat: datetime | None = None
    status: Literal["active", "inactive"] = "active"


class HandshakeHelloRequest(BaseModel):
    gatewayId: str
    clientPublicKey: str


class HandshakeHelloResponse(BaseModel):
    serverPublicKey: str
    nonce: str
    sessionId: str


class HandshakeVerifyRequest(BaseModel):
    sessionId: str
    clientHmac: str


class HandshakeVerifyResponse(BaseModel):
    status: str
    message: str
    sessionToken: str


class GatewayConnectionRequest(BaseModel):
    serialNo: str
    sensorReadings: dict[str, Any] | None = None


class GatewayConnectionResponse(BaseModel):
    status: str
    message: str
    gatewayId: str | None = None
    trainId: str | None = None
class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str
    can_configure_thresholds: bool = False
    can_manage_users: bool = False
    can_view_alerts: bool = True
    can_view_archives: bool = False
    can_reset_session: bool = False
    can_view_logs: bool = False
    can_view_reports: bool = False

class UserUpdateRequest(BaseModel):
    username: str | None = None
    role: str | None = None
    password: str | None = None
    is_active: bool | None = None
    can_configure_thresholds: bool | None = None
    can_manage_users: bool | None = None
    can_view_alerts: bool | None = None
    can_view_archives: bool | None = None
    can_reset_session: bool | None = None
    can_view_logs: bool | None = None
    can_view_reports: bool | None = None
