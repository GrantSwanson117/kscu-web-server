import asyncio
import sqlite3
from datetime import datetime
from urllib import response
import formatDB
import random
import os
from datetime import datetime
from fastapi.responses import RedirectResponse

class QueryService:

    def __init__(self, newFilename): 
        self.filename = newFilename

        self.automationDJ: str = "KSCU Bot"
        self.automationShow: str = "KSCU Autoplay"
        self.automationMsgs: str = [
            "Music Never Stops", 
            "Up all Night to get Lucky", 
            "Tunes in Perpetuity", 
            "No, you're not dreaming",
            "Stream Astrakinetic",
            "Is Midterm season over yet!?", 
            "Autonomous Audio", 
            "Who are you calling scruffy looking?",
            "At This Hour!?",
            "Keep the Signal Alive",
            "Wake up SWIG",
            "I'm Batman"]
        

        self.refreshToken = os.getenv("SPOTIFY_REFRESH_TOKEN").strip()

        self.clientID = os.getenv("CLIENT_ID").strip()
        self.clientSecret = os.getenv("CLIENT_SECRET").strip()
        self.redirectURI = os.getenv("REDIRECT_URI").strip()
        self.authURL = os.getenv("AUTH_URL").strip()
        self.tokenURL = os.getenv("TOKEN_URL").strip()
        self.apiBaseURL = os.getenv("API_BASE_URL").strip()

    def set_client(self, client):
        self.client = client

    @staticmethod
    def dbTime():
        return datetime.now().hour * 60 + datetime.now().minute
        
    @staticmethod
    def dbWeekday():
        return datetime.now().weekday()
         
    async def getAccessToken(self):
        if hasattr(self, "activeToken") and self.activeToken: return self.activeToken

        response = await self.client.post(
            self.tokenURL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refreshToken,
                "client_id": self.clientID,
                "client_secret": self.clientSecret,
            },
        )

        if response.status_code != 200:
            print(f"SYSTEM: Token refresh failed ({response.status_code}): {response.text}")
            raise RuntimeError(f"Spotify token refresh failed: {response.status_code}")

        data = response.json()
        access_token = data.get("access_token")

        if not access_token:
            raise RuntimeError(f"Spotify token response missing access_token: {data}")

        self.activeToken = access_token
        asyncio.create_task(self.expireToken(3000))

        return self.activeToken
    async def expireToken(self, delay):
        await asyncio.sleep(delay)
        self.activeToken = None
        print("SYSTEM: Access token expired, will refresh on next request")

    def dbCurrentShow(self):
        weekday = self.dbWeekday()
        yesterday = (weekday - 1) % 7
        currentTime = self.dbTime()
        shows = []
        
        with sqlite3.connect(self.filename) as conn:
            try:
                conn.row_factory = sqlite3.Row   
                query = """
                    SELECT rowid, * FROM shows
                    WHERE show_title IS NOT NULL
                    AND TRIM(show_title) != ''
                    AND (
                    (
                        day_id = ?
                        AND start_time <= ?
                        AND (CASE WHEN end_time = 0 THEN 1440 ELSE end_time END) > ?
                    )
                    OR (
                            day_id = ?
                            AND start_time > (CASE WHEN end_time = 0 THEN 1440 ELSE end_time END)
                            AND (CASE WHEN end_time = 0 THEN 1440 ELSE end_time END) > ?
                        )
                    )
                """
                rows = conn.execute(query, (weekday, currentTime, currentTime, yesterday, currentTime)).fetchall()
                shows = [dict(row) for row in rows]
            except Exception: pass

        if not shows: 
            return self.getAutoplay()
        return self.handleCohosts(shows)

    def handleCohosts(self, showList):
        if not showList: return self.getAutoplay()
        firstShow = dict(showList[0])
        djList = [firstShow["dj_name"]]
        nameList = [firstShow["name"]]
        emailList = [firstShow["email"]]
        
        for show in showList[1:]:
            if show["show_title"] != firstShow["show_title"]:
                raise ValueError("SYSTEM: Show scheduling error: Multiple shows in the same timeslot.")
            elif show["dj_name"] != firstShow["dj_name"]:
                djList.append(show["dj_name"])
                emailList.append(show["email"])
                nameList.append(show["name"])
        
        firstShow["dj_name"] = self.grammaticalJoin(djList)
        firstShow["name"] = nameList
        firstShow["email"] = emailList
        return firstShow

    def dbFormat(self):
        formatDB.formatDB(self.filename)

    @staticmethod
    def grammaticalJoin(list):
        cleanedList = [i.strip() for i in list if i]
        return ", ".join(cleanedList[:-2] + [" & ".join(cleanedList[-2:])])
    
    def display(self):
        with sqlite3.connect(self.filename) as conn:
            conn.row_factory = sqlite3.Row   
            rows = conn.execute('''SELECT "day", "show_title", "dj_name", "start_time", "end_time" FROM shows''').fetchall()
            return rows        
    def getAutoplay(self):
        return {
                "show_title": self.automationShow,
                "dj_name": self.automationDJ,
                "start_time": random.choice(self.automationMsgs),
                "end_time": None
                }
    
    def dbNextShow(self):
        day = self.dbWeekday()
        start = self.dbTime()
        shows = []

        validFilter = 'AND "show_title" IS NOT NULL AND "show_title" != "" AND "show_title" != ?'
        try:
            with sqlite3.connect(self.filename) as conn:
                conn.row_factory = sqlite3.Row
                slot = conn.execute(
                    f"""
                    SELECT day_id, start_time FROM shows 
                    WHERE ((day_id > ?) OR (day_id = ? AND start_time > ?))
                    {validFilter}
                    ORDER BY day_id, start_time LIMIT 1
                    """,
                    (day, day, start, self.automationShow)
                ).fetchone()

                if slot is None:
                    slot = conn.execute(
                        f'SELECT day_id, start_time FROM shows WHERE 1=1 {validFilter} ORDER BY day_id, start_time LIMIT 1',
                        (self.automationShow,)
                    ).fetchone()

                if slot is None:
                    return self.getAutoplay()

                rows = conn.execute(
                    f'SELECT rowid, * FROM shows WHERE day_id = ? AND start_time = ? {validFilter}',
                    (slot["day_id"], slot["start_time"], self.automationShow)
                ).fetchall()
                shows = [dict(r) for r in rows]
                
        except Exception: pass

        if not shows:
            return self.getAutoplay()
        return self.handleCohosts(shows)

    async def dbCurrentTrack(self):
        token = await self.getAccessToken()
        
        response = await self.client.get(
            f"{self.apiBaseURL}/me/player/currently-playing",
            headers={"Authorization": f"Bearer {token}"}
        )

        if response.status_code == 204 or response.status_code == 404:
            return {"SYSTEM": "No track currently playing"}

        if response.status_code == 429:
            retryAfter = int(response.headers.get("Retry-After", 30))
            print(f"SYSTEM: Spotify API rate currently limited. Retrying after {retryAfter} seconds.")
            await asyncio.sleep(retryAfter)
            self.activeToken = None 

        if response.status_code == 401:
            print("SYSTEM: Spotify API token expired/invalid. Refreshing token.")
            self.activeToken = None
            return await self.dbCurrentTrack()

        if response.status_code != 200:
            return {"SYSTEM": "Unable to retrieve track info"} 
             
        data = response.json()
        track = data.get('item')
        if not track:
            return {"SYSTEM": "Unable to retrieve track info"}
        images = track.get('album', {}).get('images', [])
        releaseDate = track.get('album', {}).get('release_date', [])
        result = {
            "id": track.get('id'),
            "name": track.get('name'),
            "artists": self.grammaticalJoin([song['name'] for song in track.get('artists', [])]),
            "link": track.get('external_urls', {}).get('spotify'),
            "image": images[0]['url'] if images else None,
            "release_date": releaseDate.split("-")[0] if releaseDate else None,
        }
        self.cachedTrack = result
        return result

    async def dbRecentTracks(self):
        token = await self.getAccessToken()
        if not token:
            return RedirectResponse(url="/login")

        response = await self.client.get(
            "https://api.spotify.com/v1/me/player/recently-played?limit=15",
            headers={"Authorization": f"Bearer {token}"}
        )

        if response.status_code == 401:
            print("SYSTEM: Spotify API token expired/invalid. Refreshing token.")
            self.activeToken = None
            return await self.dbCurrentTrack()

        if response.status_code != 200:
            return []
        
        data = response.json()

        recentTracks = []
        #Using a set to avoid duplicate songs. Check if ID was already added to set, and
        #skip if yes.
        seenIDs = set()

        index = 0
        for item in data.get('items', []):
            track = item.get('track')
            if track.get('id') in seenIDs:
                continue
            images = track.get('album', {}).get('images', [])
            releaseDate = track.get('album', {}).get('release_date', [])
            timestampLocal = item.get('played_at')
            recentTracks.append({
                "index": index,
                "name": track.get('name'),
                "artists": self.grammaticalJoin([song['name'] for song in track.get('artists', [])]),
                "link": track.get('external_urls', {}).get('spotify'),
                "played_at": timestampLocal,
                "image": images[0]['url'] if images else None,
                "release_date": releaseDate.split("-")[0] if releaseDate else None,
                "id": track.get('id')
            })
            recentTracks[index]["label"] = f"track-{index}"
            index+=1
            seenIDs.add(track.get('id'))
            if len(recentTracks) >= 10: break
            
        return recentTracks
