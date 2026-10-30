"""Experimental English platform sessions; all operations require an owner."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr, StrictBool, model_validator

from app.dependencies import get_current_user_id
from app.services.course.english.clients import PlatformError
from app.services.course.english.manager import english_manager

class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                response = await original(request)
                response.headers['Cache-Control'] = 'no-store'
                return response
            except RequestValidationError:
                # Pydantic's default error can echo passwords or imported cookies.
                raise HTTPException(422, '请求参数不正确，请检查输入') from None
        return handler


router = APIRouter(route_class=PrivateRoute)


class PasswordLoginRequest(BaseModel):
    platform: Literal['welearn', 'unipus_ai']
    username: SecretStr = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=256)


class LoginActionRequest(BaseModel):
    action: Literal['click', 'drag', 'text', 'key', 'refresh', 'scroll', 'reload', 'top']
    x: int = Field(default=0, ge=0, lt=1000, strict=True)
    y: int = Field(default=0, ge=0, lt=760, strict=True)
    end_x: int = Field(default=0, ge=0, lt=1000, strict=True)
    end_y: int = Field(default=0, ge=0, lt=760, strict=True)
    text: SecretStr = Field(default=SecretStr(''), max_length=256)
    key: Literal['Tab', 'Backspace', 'Delete', 'Enter', 'Escape', 'ArrowLeft', 'ArrowRight', 'Space'] = 'Tab'
    delta_y: int = Field(default=0, ge=-600, le=600, strict=True)


class ConnectRequest(BaseModel):
    platform: Literal['welearn', 'unipus_ai']
    credential: SecretStr = Field(min_length=1, max_length=100000)
    course_url: str = Field(default='', max_length=4096)


class ItemsRequest(BaseModel):
    course: str = Field(min_length=1, max_length=4096)


class StartRequest(ItemsRequest):
    item: str = Field(min_length=1, max_length=256)
    end_item: str = Field(default='', max_length=256)
    mode: Literal['study', 'answer'] = 'study'
    submit: StrictBool = False
    allow_ai: StrictBool = False
    recording_bypass: StrictBool = False
    seconds: int = Field(default=60, ge=30, le=1800, strict=True)


    @model_validator(mode='after')
    def answering_options(self):
        if self.mode != 'answer' and (self.submit or self.allow_ai):
            raise ValueError('自动提交与 AI 选项仅适用于自动答题模式')
        if self.recording_bypass and (self.mode != 'answer' or not self.submit):
            raise ValueError('录音实验处理需要自动答题和自动提交')
        return self


async def call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except PlatformError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/logins')
async def password_login(request: PasswordLoginRequest, owner=Depends(get_current_user_id)):
    return await call(english_manager.logins.start, owner, request.platform,
                      request.username.get_secret_value(), request.password.get_secret_value())


@router.post('/logins/wechat')
async def wechat_login(owner=Depends(get_current_user_id)):
    return await call(english_manager.logins.start_wechat, owner)


@router.get('/logins')
async def pending_logins(owner=Depends(get_current_user_id)):
    return {'logins': await call(english_manager.logins.list, owner)}


@router.get('/logins/{lid}')
async def login_status(lid: str, owner=Depends(get_current_user_id)):
    return await call(english_manager.logins.exchange, owner, lid)


@router.post('/logins/{lid}/actions')
async def login_action(lid: str, request: LoginActionRequest, owner=Depends(get_current_user_id)):
    command = request.model_dump(exclude={'text'})
    command['text'] = request.text.get_secret_value()
    return await call(english_manager.logins.exchange, owner, lid, command)


@router.delete('/logins/{lid}')
async def cancel_login(lid: str, owner=Depends(get_current_user_id)):
    await call(english_manager.logins.cancel, owner, lid)
    return {'cancelled': True}


@router.post('/sessions')
async def connect(request: ConnectRequest, owner=Depends(get_current_user_id)):
    return await call(english_manager.connect, owner, request.platform,
                      request.credential.get_secret_value(), request.course_url)


@router.post('/sessions/{sid}/items')
async def items(sid: str, request: ItemsRequest, owner=Depends(get_current_user_id)):
    return await call(english_manager.items, owner, sid, request.course)


@router.post('/sessions/{sid}/tasks')
async def start(sid: str, request: StartRequest, owner=Depends(get_current_user_id)):
    return await call(english_manager.start, owner, sid, request.course, request.item, request.seconds,
                      request.mode, request.submit, request.allow_ai, request.end_item, request.recording_bypass)


@router.delete('/sessions/{sid}')
async def disconnect(sid: str, owner=Depends(get_current_user_id)):
    await call(english_manager.disconnect, owner, sid)
    return {'disconnected': True}


@router.get('/tasks')
async def tasks(owner=Depends(get_current_user_id)):
    return {'tasks': await call(english_manager.list, owner)}


@router.post('/tasks/{tid}/stop')
async def stop(tid: str, owner=Depends(get_current_user_id)):
    return {'task': await call(english_manager.stop, owner, tid)}
