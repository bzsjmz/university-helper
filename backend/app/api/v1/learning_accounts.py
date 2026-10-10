import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_current_user_id
from app.services.course.chaoxing.managed_accounts import managed_accounts
from app.services.course.chaoxing.course_portal_service import chaoxing_course_portal_service
from app.services.course.chaoxing.endpoint_security import UnsafeEndpointError
from app.services.course.chaoxing.task_admission import TaskAdmissionError, is_active_status
from .course import _get_learning_manager, CourseStartRequest

router = APIRouter()


class AccountCreate(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=512)
    label: str = Field(default='', max_length=128)


class AccountUpdate(BaseModel):
    password: str = Field(default='', max_length=512)
    label: str = Field(default='', max_length=128)


class AccountTask(CourseStartRequest):
    platform: str = 'chaoxing'
    username: str = ''
    password: str = ''
    concurrency: int = Field(default=1, ge=1)
    speed: float = Field(default=1.0, gt=0, le=4)


def ensure_idle(owner, account_id):
    tasks = _get_learning_manager().list_tasks(str(owner))
    if any(task.get('account_id') == account_id and is_active_status(task.get('status')) for task in tasks):
        raise HTTPException(409, 'Stop this account task before editing or deleting the account')


@router.get('')
async def list_accounts(owner: str = Depends(get_current_user_id)):
    return {'accounts': await asyncio.to_thread(managed_accounts.list, owner)}


@router.post('')
async def add_account(request: AccountCreate, owner: str = Depends(get_current_user_id)):
    username = request.username.strip()
    if not username or not request.password.strip():
        raise HTTPException(422, 'Account and password must not be blank')
    return {'account': await asyncio.to_thread(managed_accounts.save, owner, username, request.password, request.label.strip())}


@router.patch('/{account_id}')
async def edit_account(account_id: str, request: AccountUpdate, owner: str = Depends(get_current_user_id)):
    if request.password and not request.password.strip():
        raise HTTPException(422, 'Password must not be blank')
    def edit():
        with managed_accounts.lock(owner, account_id):
            managed_accounts.get(owner, account_id)
            ensure_idle(owner, account_id)
            return managed_accounts.update(owner, account_id, request.password, request.label.strip())
    return {'account': await asyncio.to_thread(edit)}


@router.delete('/{account_id}')
async def remove_account(account_id: str, owner: str = Depends(get_current_user_id)):
    def remove():
        with managed_accounts.lock(owner, account_id):
            managed_accounts.get(owner, account_id)
            ensure_idle(owner, account_id)
            managed_accounts.delete(owner, account_id)
    await asyncio.to_thread(remove)
    return {'status': 'success'}


@router.get('/{account_id}/courses')
async def courses(account_id: str, owner: str = Depends(get_current_user_id)):
    def fetch():
        with managed_accounts.lock(owner, account_id):
            client = managed_accounts.client(owner, account_id)
            try:
                return client.get_courses()
            except Exception:
                managed_accounts.forget(owner, account_id)
                raise HTTPException(502, 'Course query failed; refresh to reconnect this account') from None
    return {'courses': await asyncio.to_thread(fetch)}


@router.get('/{account_id}/chapters/{course_id}')
async def chapters(account_id: str, course_id: str, owner: str = Depends(get_current_user_id)):
    def fetch():
        with managed_accounts.lock(owner, account_id):
            client = managed_accounts.client(owner, account_id)
            try:
                context = chaoxing_course_portal_service.resolve_course(course_id, client.get_courses())
                return chaoxing_course_portal_service.fetch_tab(client.session, context, 'chapters')
            except ValueError:
                raise HTTPException(400, 'Course not found in this account') from None
            except Exception:
                managed_accounts.forget(owner, account_id)
                raise HTTPException(502, 'Chapter query failed; reconnect this account') from None
    return await asyncio.to_thread(fetch)


@router.post('/{account_id}/tasks')
async def start_task(account_id: str, request: AccountTask, owner: str = Depends(get_current_user_id)):
    if not request.course_ids or len(request.course_ids) > 100:
        raise HTTPException(422, 'Select between 1 and 100 courses')
    def start():
        with managed_accounts.lock(owner, account_id):
            row, username, password = managed_accounts.credentials(owner, account_id)
            payload = request.model_dump()
            payload.update(platform='chaoxing', username=username, password=password,
                           account_id=account_id, account_key=row['account_key'],
                           account_label=row['account_label'], account_name=row['label'])
            return _get_learning_manager().start_task(str(owner), payload)
    try:
        task_id = await asyncio.to_thread(start)
    except TaskAdmissionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    except UnsafeEndpointError:
        raise HTTPException(422, 'Invalid answer-provider endpoint configuration') from None
    return {'task_id': task_id, 'status': 'started', 'message': 'Task started'}
