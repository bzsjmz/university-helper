"""Owner-scoped persistent WE Learn workbench."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictBool, model_validator

from app.dependencies import get_current_user_id
from app.services.course.english.welearn_workbench import welearn_workbench
from .english_learning import PrivateRoute, call

router = APIRouter(route_class=PrivateRoute)


class SaveLogin(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    label: str = Field(default='', max_length=128)
    account_id: str = Field(default='', max_length=64)


class Rename(BaseModel):
    label: str = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def trim(self):
        self.label = self.label.strip()
        if not self.label:
            raise ValueError('请输入名称')
        return self


class Course(BaseModel):
    course: str = Field(min_length=1, max_length=256)


class Selection(Course):
    items: list[str] = Field(min_length=1, max_length=5000)

    @model_validator(mode='after')
    def ids(self):
        if any(not item or len(item) > 256 for item in self.items):
            raise ValueError('课节不正确')
        return self


class TaskRequest(BaseModel):
    selections: list[Selection] = Field(min_length=1, max_length=200)
    seconds: int = Field(default=300, ge=30, le=1800, strict=True)
    submit: StrictBool = False
    allow_ai: StrictBool = True
    recording_bypass: StrictBool = False

    @model_validator(mode='after')
    def options(self):
        if self.recording_bypass and not self.submit:
            raise ValueError('录音实验需要自动提交')
        if sum(len(s.items) for s in self.selections) > 5000:
            raise ValueError('一次最多选择 5000 个课节')
        return self


@router.get('/accounts')
async def accounts(owner=Depends(get_current_user_id)):
    return {'accounts': await call(welearn_workbench.accounts, owner)}


@router.post('/accounts')
async def save_login(request: SaveLogin, owner=Depends(get_current_user_id)):
    return {'account': await call(welearn_workbench.save_session, owner, request.session_id,
                                  request.label.strip(), request.account_id)}


@router.patch('/accounts/{aid}')
async def rename(aid: str, request: Rename, owner=Depends(get_current_user_id)):
    await call(welearn_workbench.rename, owner, aid, request.label)
    return {'updated': True}


@router.delete('/accounts/{aid}')
async def delete_account(aid: str, owner=Depends(get_current_user_id)):
    await call(welearn_workbench.delete_account, owner, aid)
    return {'deleted': True}


@router.get('/accounts/{aid}/courses')
async def courses(aid: str, owner=Depends(get_current_user_id)):
    return await call(welearn_workbench.courses, owner, aid)


@router.post('/accounts/{aid}/items')
async def items(aid: str, request: Course, owner=Depends(get_current_user_id)):
    return await call(welearn_workbench.items, owner, aid, request.course)


@router.post('/accounts/{aid}/tasks')
async def start(aid: str, request: TaskRequest, owner=Depends(get_current_user_id)):
    return {'task': await call(welearn_workbench.start, owner, aid, request.model_dump())}


@router.get('/tasks')
async def tasks(owner=Depends(get_current_user_id)):
    return {'tasks': await call(welearn_workbench.list_tasks, owner)}


@router.post('/tasks/{tid}/stop')
async def stop(tid: str, owner=Depends(get_current_user_id)):
    return {'task': await call(welearn_workbench.stop, owner, tid)}


@router.post('/tasks/{tid}/restart')
async def restart(tid: str, owner=Depends(get_current_user_id)):
    return {'task': await call(welearn_workbench.restart, owner, tid)}


@router.delete('/tasks/{tid}')
async def delete_task(tid: str, owner=Depends(get_current_user_id)):
    await call(welearn_workbench.delete_task, owner, tid)
    return {'deleted': True}
