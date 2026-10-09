import os, httpx, json, asyncio, logging, gc
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from datetime import datetime
from sse_starlette.sse import EventSourceResponse
from fastapi.middleware.cors import CORSMiddleware
from queryService import QueryService
from eventManager import SSEEventManager, EventModel
from dotenv import load_dotenv
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Gauge, Histogram, Summary
from showRecorder import ShowRecorder

load_dotenv()

tokenURL = os.getenv("TOKEN_URL").strip()
clientID = os.getenv("CLIENT_ID").strip()
clientSecret = os.getenv("CLIENT_SECRET").strip()
authURL = os.getenv("AUTH_URL").strip()
redirectURI = os.getenv("REDIRECT_URI").strip()


class GlobalState:
    activeToken = None
    
state = GlobalState()

#Database service instantiation
db = QueryService('schedule.db')
db.dbFormat()
eventManager = SSEEventManager()

#Show recorder instantiation
rc = ShowRecorder(db)

sportsStatus = {"is_live": False}
sportsPollInterval = 1800 #Checks if KSCU sports is live every 15 minutes

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        db.client = client
        
        trackTask = asyncio.create_task(trackWatchdog())
        showTask = asyncio.create_task(showWatchdog())
        sportsTask = asyncio.create_task(sportsWatchdog())
        recorderTask = asyncio.create_task(asyncio.to_thread(rc.run))
        
        yield
        
        trackTask.cancel()
        showTask.cancel()
        recorderTask.cancel()
        sportsTask.cancel()

app = FastAPI(lifespan = lifespan)

Instrumentator().instrument(app).expose(app)

instrumentator = Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_respect_env_var=False,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/metrics", "/healthcheck"],
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kscu.org",
                   "http://localhost:8000",
                   "http://127.0.0.1:8000",
                   "http://localhost:1313",
                   "http://127.0.0.1:1313",],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)

#Prometheus metrics
ACTIVE_LISTENERS = Gauge("kscu_active_listeners", "Current SSE subscriber count")
SPORTS_LIVE = Gauge("kscu_sports_live", "Whether KSCU Sports is live (1/0)")

class QuietLogger(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        #Old stubborn URLs. Deprecated.
        msg = record.getMessage()
        return "/spins/get" not in msg and "/spins/update" not in msg and "shows/get" not in msg and "/shows/update"

logger = logging.getLogger("uvicorn.access")
logger.addFilter(QuietLogger())

async def trackWatchdog():
    prevtrackID = None
    while True:
        try:
            track = await db.dbCurrentTrack()
            
            if track and "id" in track:
                currTrackID = track["id"]
                if currTrackID != prevtrackID:
                    await eventManager.emit(EventModel(
                    type="trackUpdate",
                    message=f"{track['name']} - {track['artists']}"
                    ))
                    prevtrackID = currTrackID
                        
        except Exception as e:
            print(f"Watchdog Error: {e}")

        if datetime.now().minute == 0:
            gc.collect()
        await asyncio.sleep(30)

async def showWatchdog():
    prevShow = prevViewers = None
    while True:
        try:
            show = db.dbCurrentShow()
            if show:
                currShow = show.get("show_title")
                if currShow != prevShow:
                    await eventManager.emit(EventModel(
                        type="showUpdate",
                        message=f"{show['show_title']} - {show['dj_name']}"
                    ))
                    prevShow = currShow
                    print(f"SYSTEM: New Show: '{show['show_title']}' - {show['dj_name']}")
            currViewers = eventManager.getViewers()
            if currViewers != prevViewers:
                await eventManager.emit(EventModel(
                    type="viewsUpdate",
                    message=str(currViewers)))
                ACTIVE_LISTENERS.set(currViewers)
                prevViewers = currViewers
                print(f"SYSTEM: Viewer count: {currViewers}")

            show = None 
            del show, currViewers

        except Exception as e:
            print(f"Watchdog Error: {e}")
            
        if datetime.now().minute == 0:
            gc.collect()
        await asyncio.sleep(30)

async def getSportsLive():
    async with httpx.AsyncClient() as client:
        response = await client.get("http://api.mixlr.com/users/kscusports")
        return {"is_live": response.json().get("is_live")}
    
async def sportsWatchdog():
    lastStatus = None
    currentStatus = await getSportsLive()

    while True:
        result = await getSportsLive()
        currentStatus = result["is_live"]
        if currentStatus != lastStatus:
            lastStatus = currentStatus
            sportsStatus["is_live"] = currentStatus
            await eventManager.emit(EventModel(
                type="sportsLiveUpdate",
                message="true" if currentStatus else "false"))
        await asyncio.sleep(sportsPollInterval)


@app.get("/")
def root(): return {
    "Hello!": "welcome to the KSCU web server! the following endpoints are used for data fetching and monitoring.",
    "/": "root, for help and info",
    "/shows/current": "current show on air",
    "/stream": "real-time SSE stream for new shows and tracks",
    "/shows/next": "next show scheduled to play (skipping automated shows)",
    "/tracks/current": "current track on air",
    "/tracks/recent": "returns the 10 most recently played tracks",
    "/schedule": "A display of every show in the current DJ schedule",
    "/healthcheck": "checks the health of the server and show recorder",
    "/metrics": "Prometheus metrics endpoint for reliability stats and viewer counts"
}

@app.get("/healthcheck")
def healthCheck(): return {"Server Status:": "OK", "Recorder Status": rc.recorderHealthCheck()}

#Legacy URL
@app.get("/spins/get")
@app.get("/tracks/current/")
@app.get("/tracks/current")
async def currentTrack():
    return await db.dbCurrentTrack()

#Returns 20 most recent tracks
@app.get("/tracks/recent/")
@app.get("/tracks/recent")
async def recentTracks():
    return await db.dbRecentTracks()

@app.get("/shows/current/")
@app.get("/shows/current")
async def currentShow():
    return db.publicShow(db.dbCurrentShow())

@app.get("/get-token")
async def get_my_token(code: str):
    response = await db.client.post(
        tokenURL,
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirectURI,
            'client_id': clientID,
            'client_secret': clientSecret,
        }
    )
    return response.json()
    
@app.get("/shows/next/")
@app.get("/shows/next")
def getNextShow():
    return db.publicShow(db.dbNextShow())

@app.get("/schedule")
def displaySchedule():
    return db.display()
        
@app.get("/stream")
async def streamEvents(request: Request):
    queue = await eventManager.subscribe()
    await queue.put({
    "event": "sportsLiveUpdate",
    "data": "true" if sportsStatus["is_live"] else "false"})

    async def streamGenerator(q):
        try:
            while True:
                if await request.is_disconnected(): 
                    break

                try:
                    event = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield {
                        "event": event["event"],
                        "data": event["data"]
                    }
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except Exception as err:
            print(f"Stream error: {err}")
        finally:
            await eventManager.unsubscribe(q)

    return EventSourceResponse(streamGenerator(queue))

@app.post("/emit")
async def newEvent(event: EventModel):
    await eventManager.emit(event)
    return {"message": f"Event '{event.type}' sent to {len(eventManager.subscribers)} listeners"}
