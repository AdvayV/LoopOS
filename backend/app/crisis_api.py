import asyncio
from copy import deepcopy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from .crisis import CrisisWorld, TOTAL, TERMINAL
from .codex_bridge import CodexBridge


class Choice(BaseModel):
    value: str


class CrisisService:
    def __init__(self, store):
        self.world = CrisisWorld(store)
        self.codex = CodexBridge()
        self.mode = 'demo'
        self.worker = None
        self.busy = False
        self.connection = {'signed_in': False, 'message': 'Check Codex connection to use your ChatGPT sign-in.'}

    def snapshot(self):
        return dict(self.world.snapshot(), mode=self.mode, busy=self.busy, connection=self.connection)

    async def step(self):
        if self.busy:
            raise ValueError('An agent turn is still finishing. Wait for its safe boundary.')
        self.busy = True
        item = None
        try:
            item = self.world.select()
            if not item:
                return
            if item['stage'] == 'plan':
                if self.mode == 'codex':
                    try:
                        plan = await asyncio.to_thread(self.codex.plan, deepcopy(item), self.world.budget, TOTAL)
                        source = 'Codex'
                    except Exception as exc:
                        self.world.status = 'paused'
                        self.world.log('connection', f'Codex could not finish: {exc}. Retry or choose Demo mode explicitly.', item['id'])
                        return
                else:
                    plan = self.world.demo_plan(item)
                    source = 'Demo'
                self.world.propose(item, plan, source)
            else:
                self.world.advance(item)
        finally:
            self.world.active = None
            self.busy = False

    async def run(self):
        try:
            while self.world.status == 'running':
                await self.step()
                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.world.status = 'paused'
            self.world.log('error', f'Exercise paused: {exc}')
        finally:
            self.world.store.set_run_status(self.world.run_id, self.world.status)


def make_router(service):
    router = APIRouter(prefix='/api/crisis')

    @router.get('/state')
    async def state():
        return service.snapshot()

    @router.post('/incidents')
    async def incident(choice: Choice):
        try:
            service.world.add(choice.value)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return service.snapshot()

    @router.post('/control/{action}')
    async def control(action: str):
        world = service.world
        if action in {'reset', 'step', 'invalid-plan'} and service.busy:
            raise HTTPException(409, 'Wait for the current Codex turn to finish.')
        if action == 'start':
            world.status = 'running'
            world.store.set_run_status(world.run_id, 'running')
            if not service.worker or service.worker.done():
                service.worker = asyncio.create_task(service.run())
        elif action == 'pause':
            world.status = 'paused'
            world.log('pause', 'Pause requested. Any active turn finishes before scheduling stops.')
        elif action == 'reset':
            if service.worker and not service.worker.done():
                service.worker.cancel()
                try:
                    await service.worker
                except asyncio.CancelledError:
                    pass
            world.reset()
        elif action == 'step':
            if world.status == 'running':
                raise HTTPException(409, 'Pause before stepping manually.')
            await service.step()
        elif action == 'invalid-plan':
            candidates = [i for i in world.incidents if i['stage'] in {'plan', 'reserve'} and i['status'] not in TERMINAL]
            if not candidates:
                raise HTTPException(409, 'Add an incident first or reset the exercise.')
            item = candidates[-1]
            world.propose(item, world.demo_plan(item, invalid=True), 'Injected invalid demo')
        else:
            raise HTTPException(404, 'Unknown control')
        return service.snapshot()

    @router.post('/mode')
    async def mode(choice: Choice):
        if service.busy or service.world.status == 'running':
            raise HTTPException(409, 'Pause and wait for the current turn before changing mode.')
        if choice.value not in {'demo', 'codex'}:
            raise HTTPException(422, 'Choose demo or codex')
        if choice.value == 'codex' and not service.connection.get('signed_in'):
            raise HTTPException(409, 'Check your Codex ChatGPT connection first.')
        service.mode = choice.value
        service.world.log('mode', f'Planning mode changed to {service.mode}.')
        return service.snapshot()

    @router.post('/codex/check')
    async def check():
        if service.busy:
            raise HTTPException(409, 'Wait for the current turn.')
        service.connection = await asyncio.to_thread(service.codex.status)
        return service.snapshot()

    @router.post('/incidents/{incident_id}/stop')
    async def stop(incident_id: str):
        if service.busy:
            raise HTTPException(409, 'Wait for the current turn to finish before stopping a task.')
        try:
            item = service.world.get(incident_id)
        except StopIteration:
            raise HTTPException(404, 'Unknown incident')
        if item['status'] in TERMINAL:
            raise HTTPException(409, 'Incident already finished.')
        service.world.stop(item)
        return service.snapshot()

    return router
