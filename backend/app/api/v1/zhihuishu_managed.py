"""Managed Zhihuishu APIs. Credentials never appear in public responses."""
import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.dependencies import get_current_user_id
from app.services.course.zhihuishu.managed import ManagedError, zhihuishu_manager

router = APIRouter()
logger = logging.getLogger(__name__)


async def call(func, *args, **kwargs):
    try:
        result = await asyncio.to_thread(func, *args, **kwargs)
        return {"status": "success", "data": result}
    except ManagedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except Exception as exc:
        # Keep operation/class for diagnostics; never log args or exception text
        # which may contain passwords, cookies or signed platform URLs.
        logger.error("Managed Zhihuishu operation failed: operation=%s exception=%s",
                     getattr(func, "__name__", type(func).__name__), type(exc).__name__)
        raise HTTPException(status_code=503, detail="智慧树服务暂不可用，请稍后重试。") from None


class Login(BaseModel):
    label: str = Field(default="", max_length=64)
    account_id: str | None = Field(default=None, max_length=64)


class PasswordLogin(Login):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=512)


class Rename(BaseModel):
    label: str = Field(min_length=1, max_length=64)

    @field_validator("label")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("账号备注不能为空")
        return value.strip()


class Selection(BaseModel):
    course_id: str = Field(min_length=1, max_length=128)
    end_chapter_id: str | None = Field(default=None, max_length=128)

    @field_validator("course_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("课程编号不能为空")
        return value.strip()


class QueueStart(BaseModel):
    courses: list[Selection] = Field(min_length=1, max_length=1000)
    speed: float = Field(default=1.0, ge=0.5, le=2)
    task_type: Literal["course", "ai-course"] = "course"
    auto_answer: bool = True


@router.get("/accounts")
async def accounts(owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.accounts, owner)


@router.post("/login/password")
async def password_login(request: PasswordLogin, owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.password_login, owner, **request.model_dump())


@router.post("/login/qr")
async def qr_login(request: Login, owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.qr_login, owner, **request.model_dump())


@router.get("/login/qr/{session_id}")
async def qr_status(session_id: str, owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.qr_status, owner, session_id)


@router.patch("/accounts/{account_id}")
async def rename(account_id: str, request: Rename, owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.rename, owner, account_id, request.label)


@router.delete("/accounts/{account_id}")
async def delete(account_id: str, owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.delete, owner, account_id)


@router.get("/accounts/{account_id}/courses")
async def courses(account_id: str, owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.courses, owner, account_id)


@router.get("/accounts/{account_id}/videos/{course_id}")
async def videos(account_id: str, course_id: str, owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.videos, owner, account_id, course_id)


@router.post("/accounts/{account_id}/queue")
async def start_queue(account_id: str, request: QueueStart, owner: str = Depends(get_current_user_id)):
    payload = request.model_dump()
    selections = payload.pop("courses")
    return await call(zhihuishu_manager.start_queue, owner, account_id, selections, **payload)


@router.get("/queues")
async def queues(account_id: str | None = None, owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.list_queues, owner, account_id)


@router.post("/queues/{queue_id}/{action}")
async def control(queue_id: str, action: Literal["pause", "resume", "cancel"], owner: str = Depends(get_current_user_id)):
    return await call(zhihuishu_manager.control, owner, queue_id, action)
