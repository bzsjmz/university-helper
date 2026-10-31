"""Local settings endpoints; this router is registered only in desktop mode."""
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from app.desktop_settings import FONT_MAP_LIMIT, LOCK, apply_settings, private_write, public_settings, validate_font_map
from app.services.course.chaoxing.endpoint_security import assert_public_endpoint, UnsafeEndpointError

router = APIRouter()


class DesktopSettings(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(max_length=128)
    primary_key: SecretStr | None = None
    fallback_key: SecretStr | None = None


def directory():
    value = os.environ.get('UH_DESKTOP_DATA')
    if not value:
        raise HTTPException(503, '桌面配置目录未就绪')
    return Path(value)


@router.get('/settings')
def get_settings():
    return public_settings(directory())


@router.put('/font-map')
async def import_font_map(request: Request):
    if request.headers.get('origin') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '仅允许在本机应用中导入字体映射')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > FONT_MAP_LIMIT:
            raise HTTPException(413, '字体映射文件过大（最多 4 MiB）')
        raw.extend(chunk)
    try:
        data = validate_font_map(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with LOCK:
        target = directory() / 'resource'
        target.mkdir(mode=0o700, exist_ok=True)
        private_write(target / 'font_map_table.json', json.dumps(data, separators=(',', ':')))
    return {'entries': len(data), 'restart_required': True}


@router.put('/settings')
def update_settings(payload: DesktopSettings, request: Request):
    if request.headers.get('origin') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '仅允许在本机应用中修改设置')
    try:
        endpoint = assert_public_endpoint(payload.endpoint)
    except UnsafeEndpointError:
        raise HTTPException(422, '请填写有效的公网 AI 接口地址') from None
    keys = {role: value.get_secret_value().strip() if value is not None else None
            for role, value in (('primary', payload.primary_key), ('fallback', payload.fallback_key))}
    if any(value is not None and (len(value) > 4096 or '\n' in value or '\r' in value) for value in keys.values()):
        raise HTTPException(422, 'API Key 格式无效')
    with LOCK:
        target = directory()
        previous = public_settings(target)
        # A changed host must never receive credentials saved for the old API.
        if previous['endpoint'] != endpoint:
            keys = {role: value or '' for role, value in keys.items()}
        for role, value in keys.items():
            if value is not None:
                private_write(target / f'ai-{role}.key', value)
        private_write(target / 'desktop-settings.json', json.dumps(
            {'endpoint': endpoint, 'model': payload.model.strip()}, ensure_ascii=False))
        apply_settings(target)
        return public_settings(target)
