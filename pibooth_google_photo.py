# -*- coding: utf-8 -*-

"""Pibooth plugin to upload pictures on Google Photos."""

import os
import json
import os.path

import requests
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
except ImportError:
    InstalledAppFlow = None
    pass  # When running the setup.py, google-auth-oauthlib is not yet installed

import pibooth
from pibooth.utils import LOGGER


__version__ = "1.2.3"

SECTION = 'GOOGLE'
CACHE_FILE = '/home/pi/Desktop/googlephotos_token.json'


@pibooth.hookimpl
def pibooth_configure(cfg):
    """Declare the new configuration options"""
    cfg.add_option(SECTION, 'album_name', "Pibooth",
                   "Album where pictures are uploaded",
                   "Album name", "Pibooth")
    cfg.add_option(SECTION, 'client_id_file', '',
                   "Credentials file downloaded from Google API")


# stop removing token
#@pibooth.hookimpl
#def pibooth_reset(cfg, hard):
#    """Remove cached token file."""
#    if hard and os.path.isfile(cfg.join_path(CACHE_FILE)):
#        LOGGER.info("Remove Google Photos autorizations '%s'", cfg.join_path(CACHE_FILE))
#        os.remove(cfg.join_path(CACHE_FILE))


## TODO check if credentials get removed when startup??
@pibooth.hookimpl
def pibooth_startup(app, cfg):
    """Create the GooglePhotosUpload instance."""
    app.previous_picture_url = None
    client_id_file = cfg.getpath(SECTION, 'client_id_file')

    if not client_id_file:
        LOGGER.debug("No credentials file defined in [GOOGLE][client_id_file], upload deactivated")
    elif not os.path.exists(client_id_file):
        LOGGER.error("No such file [%s][client_id_file]='%s', please check config",
                     SECTION, client_id_file)
    elif client_id_file and os.path.getsize(client_id_file) == 0:
        LOGGER.error("Empty file [%s][client_id_file]='%s', please check config",
                     SECTION, client_id_file)
    else:
        LOGGER.info("Initialize Google Photos connection, client id file: '%s', cache file:'%s'",client_id_file,CACHE_FILE)
        app.google_photos = GooglePhotosApi(client_id_file, CACHE_FILE)


@pibooth.hookimpl
def state_processing_exit(app, cfg):
    """Upload picture to google photo album"""
    if hasattr(app, 'google_photos'):
        album_id = False
        try:
            album_id = cfg.get(SECTION, 'album_id')
        except:
            album_id = False
        album_name = cfg.get(SECTION, 'album_name')
        photo_id = app.google_photos.upload(app.previous_picture_file, album_id, album_name)

        if photo_id is not None:
            app.previous_picture_url = app.google_photos.get_temp_url(photo_id)
        else:
            app.previous_picture_url = None


class GooglePhotosApi(object):

    """Google Photos interface.

    A file with YOUR_CLIENT_ID and YOUR_CLIENT_SECRET is required, go to
    https://developers.google.com/photos/library/guides/get-started .

    A file ``token_file`` is generated at first run to store permanently the
    autorizations to use Google API.

    :param client_id: file generated from google API
    :type client_id: str
    :param token_file: file where generated token will be stored
    :type token_file: str
    """

    URL = 'https://photoslibrary.googleapis.com/v1'
    SCOPES = ['https://www.googleapis.com/auth/photoslibrary',
              'https://www.googleapis.com/auth/photoslibrary.sharing']

    def __init__(self, client_id_file, token_file="token.json"):
        self.client_id_file = client_id_file
        self.token_cache_file = token_file

        self._albums_cache = {}  # Keep cache to avoid multiple request
        if self.is_reachable():
            self._session = self._get_authorized_session()
        else:
            self._session = None

    def _auth(self):
        """Open browser to create credentials."""
        # TODO TEST
        #if self._window.is_fullscreen:
        #    self._window.toggle_fullscreen()
        flow = InstalledAppFlow.from_client_secrets_file(self.client_id_file, scopes=self.SCOPES)
        return flow.run_local_server(port=0)

    def _save_credentials(self, credentials):
        """Save credentials in a file to use API without need to allow acces."""
        try:
            with open(self.token_cache_file, 'w') as fp:
                fp.write(credentials.to_json())
        except OSError as err: 
            LOGGER.warning("Can not save Google Photos token in file '%s': %s",
                           self.token_cache_file, err)

    def _get_authorized_session(self):
        """Create credentials file if required and open a new session."""
        credentials = None
        LOGGER.info("Get_authorized_session: client id file '%s', cache file:'%s'",self.client_id_file, self.token_cache_file)
        if not os.path.exists(self.token_cache_file) or \
                os.path.getsize(self.token_cache_file) == 0:
            LOGGER.debug("First use of plugin, store token in file '%s'", #### TODO WHY FIRST USE OF PLUGIN???
                         self.token_cache_file)
            credentials = self._auth()
            self._save_credentials(credentials)
        else:
            credentials = Credentials.from_authorized_user_file(self.token_cache_file, self.SCOPES)
            with open(self.client_id_file) as fd:
                data = json.load(fd)
                if "web" in data:
                    data = data["web"]
                elif "installed" in data:
                    data = data["installed"]

            if credentials.client_id != data.get('client_id') or\
                    credentials.client_secret != data.get('client_secret'):
                LOGGER.debug("Application key or secret has changed, store new token in file '%s'",
                             self.token_cache_file)
                credentials = self._auth()
                self._save_credentials(credentials)
            elif credentials.expired:
                credentials.refresh(Request())
                self._save_credentials(credentials)

        if credentials:
            return AuthorizedSession(credentials)
        return None

    def is_reachable(self):
        """Check if Google Photos is reachable."""
        try:
            return requests.head('https://photos.google.com').status_code in (200, 302)
        except requests.ConnectionError:
            return False

    def get_albums(self, app_created_only=False):
        """Generator to loop through all Google Photos albums."""
        params = {
            'excludeNonAppCreatedData': app_created_only
        }
        while True:
            albums = self._session.get(self.URL + '/albums', params=params).json()
            LOGGER.debug("Google Photos server response: %s", albums)

            if 'albums' in albums:
                for album in albums["albums"]:
                    yield album
                if 'nextPageToken' in albums:
                    params["pageToken"] = albums["nextPageToken"]
                else:
                    return  # close generator
            else:
                return  # close generator

    def get_album_id(self, album_name):
        """Return the album ID if exists else None."""
        if album_name.lower() in self._albums_cache:
            return self._albums_cache[album_name.lower()]["id"]

        for album in self.get_albums(False):
            title = album["title"].lower()
            self._albums_cache[title] = album
            if title == album_name.lower():
                LOGGER.info("Found existing Google Photos album '%s'", album_name)
                return album["id"]
            LOGGER.info("!!! album '%s' not found", album_name)
        return None
    
    def get_album_by_id(self, album_id):
        """Return the album ID if exists else None."""
        if album_id in self._albums_cache:
            return self._albums_cache[album_id]["id"]
        LOGGER.info("!!! ALBUM IS NOT IN CACHE !!! %s", album_id)
        for album in self.get_albums(False):
            id = album["id"]
            self._albums_cache[id] = album
            LOGGER.info("Listing album '%s'", id)
            if id == album_id:
                LOGGER.info("Found existing Google Photos album '%s'", album_id)
                return album["id"]
        LOGGER.info("!!! ALBUM ID NOT FOUND !!! %s", album_id)
        return None

    def create_album(self, album_name):
        """Create a new album and return its ID."""
        LOGGER.info("Creating a new Google Photos album '%s'", album_name)
        create_album_body = json.dumps({"album": {"title": album_name}})

        resp = self._session.post(self.URL + '/albums', create_album_body).json()
        LOGGER.debug("Google Photos server response: %s", resp)

        if "id" in resp:
            return resp['id']

        LOGGER.error("Can not create Google Photos album '%s'", album_name)
        return None

    def upload(self, filename, config_album_id, config_album_name):
        """Upload a photo file to the given Google Photos album.

        :param filename: photo file full path
        :type filename: str
        :param album_name: name of albums to upload
        :type album_name: str

        :returns: uploaded photo ID
        :rtype: str
        """
        photo_id = None

        if not self.is_reachable():
            LOGGER.error("Google Photos upload failure: no internet connexion!")
            return photo_id

        if not self._session:
            # Plugin was disabled at startup but activated after
            self._session = self._get_authorized_session()

        album_id = False
        if(config_album_id):
            album_id = config_album_id #self.get_album_by_id(config_album_id)
        else:
            album_id = self.get_album_id(config_album_name)
        
        if not album_id:
            album_id = self.create_album(config_album_name)
        if not album_id:
            LOGGER.error("Google Photos upload failure: album '%s' not found!", config_album_name)
            return photo_id

        self._session.headers["Content-type"] = "application/octet-stream"
        self._session.headers["X-Goog-Upload-Protocol"] = "raw"

        with open(filename, mode='rb') as fp:
            data = fp.read()

        self._session.headers["X-Goog-Upload-File-Name"] = os.path.basename(filename)

        LOGGER.info("Uploading picture '%s' to Google Photos", filename)
        upload_resp = self._session.post(self.URL + '/uploads', data)

        if upload_resp.status_code == 200 and upload_resp.content:
            create_body = json.dumps(
                {
                    "albumId": album_id,
                    "newMediaItems": [
                        {
                            "description": "",
                            "simpleMediaItem": {
                                "uploadToken": upload_resp.content.decode()
                            }
                        }
                    ]
                })

            resp = self._session.post(self.URL + '/mediaItems:batchCreate', create_body).json()
            LOGGER.debug("Google Photos server response: %s", resp)

            if "newMediaItemResults" in resp:
                status = resp["newMediaItemResults"][0]["status"]
                if status.get("code") and (status.get("code") > 0):
                    LOGGER.error("Google Photos upload failure: can not add '%s' to library: %s",
                                 os.path.basename(filename), status["message"])
                else:
                    LOGGER.info("Google Photos upload successful: '%s' added to album '%s'",
                                os.path.basename(filename), album_id)

                    photo_id = resp["newMediaItemResults"][0]['mediaItem']['id']
            else:
                LOGGER.error("Google Photos upload failure: can not add '%s' to library",
                             os.path.basename(filename))

        elif upload_resp.status_code != 200:
            LOGGER.error("Google Photos upload failure: can not connect to '%s' (HTTP error %s)",
                         self.URL, upload_resp.status_code)
        else:
            LOGGER.error("Google Photos upload failure: no response content from server '%s'",
                         self.URL)

        try:
            del self._session.headers["Content-type"]
            del self._session.headers["X-Goog-Upload-Protocol"]
            del self._session.headers["X-Goog-Upload-File-Name"]
        except KeyError:
            pass

        return photo_id

    def get_temp_url(self, photo_id):
        """
        Get the temporary URL for the picture (valid 1 hour only).
        """
        resp = self._session.get(self.URL + '/mediaItems/' + photo_id)
        if resp.status_code == 200:
            url = resp.json()['baseUrl']
            LOGGER.debug('Temporary picture URL -> %s', url)
            return url

        LOGGER.warning("Can not get temporary URL for Google Photos")
        return None
