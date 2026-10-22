"""Experimental English platform sessions; all operations require an owner."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, StrictBool, model_validator

from app.dependencies import get_current_user_id
from app.services.course.english.clients import PlatformError
from app.services.course.english.manager import english_manager

router = APIRouter()


class ConnectRequest(BaseModel):
    platform: Literal['welearn', 'unipus']
    credential: SecretStr = Field(min_length=1, max_length=100000)
    course_url: str = Field(default='', max_length=4096)


class ItemsRequest(BaseModel):
    course: str = Field(min_length=1, max_length=4096)


class StartRequest(ItemsRequest):
    item: str = Field(min_length=1, max_length=256)
    mode: Literal['study', 'answer'] = 'study'
    submit: StrictBool = False
    allow_ai: StrictBool = False
    seconds: int = Field(default=60, ge=30, le=1800, strict=True)


    @model_validator(mode='after')
    def answering_options(self):
        if self.mode != 'answer' and (self.submit or self.allow_ai):
            raise ValueError('自动提交与 AI 选项仅适用于自动答题模式')
        return self


async def call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except PlatformError as exc:
        raise HTTPException(400, str(exc)) from None


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
                      request.mode, request.submit, request.allow_ai)


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
