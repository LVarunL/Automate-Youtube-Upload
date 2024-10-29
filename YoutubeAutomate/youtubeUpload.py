import http.client as httplib
import httplib2
import os
import random
import sys
import time

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow

# Explicitly tell the underlying HTTP transport library not to retry, since
# we are handling retry logic ourselves.
httplib2.RETRIES = 1
MAX_RETRIES = 10
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, httplib.NotConnected,
                        httplib.IncompleteRead, httplib.ImproperConnectionState,
                        httplib.CannotSendRequest, httplib.CannotSendHeader,
                        httplib.ResponseNotReady, httplib.BadStatusLine)
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]


CLIENT_SECRETS_FILE = "client_secrets.json"
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"


MISSING_CLIENT_SECRETS_MESSAGE = """
WARNING: Please configure OAuth 2.0

To make this sample run you will need to populate the client_secrets.json file
found at:

   %s

with information from the API Console
https://console.cloud.google.com/

For more information about the client_secrets.json file format, please visit:
https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
""" % os.path.abspath(os.path.join(os.path.dirname(__file__),
                                   CLIENT_SECRETS_FILE))

VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}
TEXT_EXTENSIONS = {'.txt', '.md'}

def get_authenticated_service(args):
    flow = flow_from_clientsecrets(CLIENT_SECRETS_FILE,
                                   scope=YOUTUBE_UPLOAD_SCOPE,
                                   message=MISSING_CLIENT_SECRETS_MESSAGE)

    storage = Storage("%s-oauth2.json" % sys.argv[0])
    credentials = storage.get()

    # if credentials is None or credentials.invalid:
    credentials = run_flow(flow, storage, args)

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION,
                 http=credentials.authorize(httplib2.Http()))


def initialize_upload(youtube, options):
    tags = None
    if options.get("keywords"):
        tags = options.get("keywords").split(",")

    body = dict(
        snippet=dict(
            title=options.get("title"),
            description=options.get("description"),
            tags=tags,
            categoryId=options.get("category"),
        ),
        status=dict(
            privacyStatus=options.get("privacyStatus")
        )
    )

    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(options.get("file"), chunksize=-1, resumable=True)
    )

    video_id = resumable_upload(insert_request)
    if video_id and options.get("thumbnail"):
        upload_thumbnail(youtube, video_id, options.get("thumbnail"))

# This method implements an exponential backoff strategy to resume a failed upload.
def resumable_upload(insert_request):
    response = None
    error = None
    retry = 0
    while response is None:
        try:
            print("Uploading file...")
            status, response = insert_request.next_chunk()
            if response is not None:
                if 'id' in response:
                    print("Video id '%s' was successfully uploaded." % response['id'])
                    return response['id']
                else:
                    exit("The upload failed with an unexpected response: %s" % response)
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = "A retriable HTTP error %d occurred:\n%s" % (e.resp.status,
                                                                     e.content)
            else:
                raise
        except RETRIABLE_EXCEPTIONS as e:
            error = "A retriable error occurred: %s" % e

        if error is not None:
            print(error)
            retry += 1
            if retry > MAX_RETRIES:
                exit("No longer attempting to retry.")

            max_sleep = 2 ** retry
            sleep_seconds = random.random() * max_sleep
            print("Sleeping %f seconds and then retrying..." % sleep_seconds)
            time.sleep(sleep_seconds)

    return None

def upload_thumbnail(youtube, video_id, thumbnail_file):
    try:
        print("Uploading thumbnail...")
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_file)
        ).execute()
        print("Thumbnail uploaded successfully.")
    except HttpError as e:
        print("An HTTP error %d occurred:\n%s" % (e.resp.status, e.content))

def find_files(folder_path):
    video_file = None
    image_file = None
    text_file = None
    errors = []

    try:
        # Check if folder exists
        if not os.path.isdir(folder_path):
            raise FileNotFoundError(f"Folder '{folder_path}' does not exist.")
        
        # Iterate over files in the directory
        for file_name in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file_name)
            
            # Ensure it's a file
            if not os.path.isfile(file_path):
                continue

            # Get the file extension
            _, ext = os.path.splitext(file_name)
            ext = ext.lower()

            # Classify file based on its extension
            if ext in VIDEO_EXTENSIONS:
                if video_file is None:
                    video_file = file_path
                else:
                    errors.append(f"Multiple video files found: '{video_file}', '{file_path}'")
            elif ext in IMAGE_EXTENSIONS:
                if image_file is None:
                    image_file = file_path
                else:
                    errors.append(f"Multiple image files found: '{image_file}', '{file_path}'")
            elif ext in TEXT_EXTENSIONS:
                if text_file is None:
                    text_file = file_path
                else:
                    errors.append(f"Multiple text files found: '{text_file}', '{file_path}'")
            else:
                errors.append(f"Unsupported file type '{file_name}' found in the folder.")

        # Check if all required files are present and unique
        if video_file is None:
            errors.append("No video file found.")
        if image_file is None:
            errors.append("No image file found.")
        if text_file is None:
            errors.append("No text file found.")

    except Exception as e:
        errors.append(f"An unexpected error occurred: {e}")

    # If there are errors, write them to 'errors.txt' in the same folder
    if errors:
        errors_file_path = os.path.join(folder_path, "errors.txt")
        with open(errors_file_path, "w") as error_file:
            error_file.write("Errors encountered:\n")
            for error in errors:
                error_file.write("- " + error + "\n")
        print(f"Errors were written to '{errors_file_path}'")
    else:
        print("All files found successfully:")

    # Return the results
    return {
        "videoFile": video_file,
        "imageFile": image_file,
        "textFile": text_file,
        "errors": errors
    }

def get_title_from_path(file_path):
    # Extract the filename with extension
    filename_with_extension = os.path.basename(file_path)
    # Split the filename and extension
    filename, _ = os.path.splitext(filename_with_extension)
    return filename

def read_text_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        return content
    except FileNotFoundError:
        print(f"Error: The file '{file_path}' was not found.")
    except IOError:
        print(f"Error: Unable to read the file '{file_path}'.")
    return None

if __name__ == '__main__':
    parameters = {}
    filesFound = find_files("C:\\Users\\vyasv\\OneDrive\\AutomateYoutube\\TestVideos\\Video1")
    parameters["file"] = filesFound.get("videoFile")
    parameters["title"] = get_title_from_path(parameters.get("file"))
    parameters["thumbnail"] = filesFound.get("imageFile")
    textFilePath = filesFound.get("textFile")
    parameters["description"] = read_text_file(textFilePath)    
    parameters["category"] = "22"
    parameters["privacyStatus"] = "public"

    args = argparser.parse_args()
    youtube = get_authenticated_service(args)
    print(youtube)
    try:
        initialize_upload(youtube, args)
    except HttpError as e:
        print("An HTTP error %d occurred:\n%s" % (e.resp.status, e.content))


