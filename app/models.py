from datetime import datetime
from typing import Literal, Any

from pydantic import BaseModel, Field


class HandshakeRequest(BaseModel):
    gatewayId: str = Field(..., examples=["GW1"])
    trainId: str = Field(..., examples=["12345"])
    gatewaySerial: str = Field(..., examples=["SN001"])
    firmwareVersion: str = Field(..., examples=["1.0"])
    clientCertPem: str | None = Field(default=None, description="Optional PEM encoded X.509 client certificate for PKI auto-provisioning")
    sshPublicKey: str | None = Field(default=None, description="Optional SSH public key for secure upload")


class UploadLeaseRequest(BaseModel):
    gatewayId: str = Field(..., examples=["GW1_20693_BOGIE_01"])
    trainId: str = Field(..., examples=["20693"])
    sessionName: str = Field(..., examples=["SESSION_20260727_103000000"])
    zipFileName: str = Field(..., examples=["GW1_20693_BOGIE_01__20693__SESSION_20260727_103000000.zip"])
    sha256: str = Field(..., examples=["0f4c2a"])
    sizeBytes: int = Field(..., examples=[523000000])


class UploadCompleteRequest(BaseModel):
    gatewayId: str | None = Field(None, examples=["GW1_20693_BOGIE_01"])
    trainId: str = Field(..., examples=["20693"])
    uploadId: str = Field(..., examples=["b8bc2cf7-3d91-4d3d-8321-5e1d46e6f001"])
    sessionName: str | None = Field(None, examples=["SESSION_20260727_103000000"])
    sha256: str = Field(..., examples=["0f4c2a"])
    sizeBytes: int = Field(..., examples=[523000000])


class AuthRequest(BaseModel):
    gatewayId: str = Field(..., examples=["GW1"])
    trainId: str = Field(..., examples=["019456"])
    apiKey: str = Field(..., examples=["123456"])
    sessionId: str = Field(..., examples=["sessionId"])


class CommandResultItem(BaseModel):
    commandId: str
    type: Literal["reset", "calibration_update"]
    status: Literal["success", "failed"]
    completedAt: datetime | None = None
    location: dict | None = None
    details: dict | None = None


class HeartbeatRequest(BaseModel):
    gatewayId: str = Field(..., examples=["GW1"])
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


class AlertRequest(BaseModel):
    gatewayId: str | None = None
    trainNo: str | None = None
    latitude: float
    longitude: float
    peakValueG: float


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

class UserUpdateRequest(BaseModel):
    username: str | None = None
    role: str | None = None
    password: str | None = None
    is_active: bool | None = None
    can_configure_thresholds: bool | None = None
    can_manage_users: bool | None = None
    can_view_alerts: bool | None = None
