import os

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/etc/secrets/firebase-creds.json"


from fastapi import FastAPI,Request,Query,Form
from fastapi.responses import HTMLResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
import starlette.status as status
from datetime import datetime
from google.cloud import storage
import local_constants

app=FastAPI()

firestore_db=firestore.Client()

firebase_request_adapter = requests.Request()

app.mount('/static',StaticFiles(directory='static'),name='static')
templates=Jinja2Templates(directory="templates")

def validateFirebaseToken(id_token):
    if not id_token:
        return None
    user_token = None
    try:
        user_token=google.oauth2.id_token.verify_firebase_token(id_token,firebase_request_adapter)   
    except ValueError as err:
        print(str(err))
    return user_token

def getAllUsernames():
    users = firestore_db.collection("User").stream()
    return [doc.to_dict().get("Username") for doc in users]


async def getFeedForUser(user_id: str):
    userDoc = firestore_db.collection("User").document(user_id).get()

    if not userDoc.exists:
        return []

    userData = userDoc.to_dict()
    followingList = userData.get("Following", [])

    ownUsername = userData.get("Username")
    followingList.append(ownUsername)

    allPosts = []

    postsCollection = firestore_db.collection("Post")

    posts_query = postsCollection\
        .where("Username", "in", followingList)\
        .order_by("Date", direction=firestore.Query.DESCENDING)\
        .limit(50)\
        .stream()

    for post in posts_query:
        allPosts.append(post.to_dict())

    return allPosts


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        
        return templates.TemplateResponse("main.html", {
            "request": request,
            "user_token": None
        })

    user_id = user_token["user_id"]
    user_email = user_token["email"]

    userCollection = firestore_db.collection("User").document(user_id)
    userDocument = userCollection.get()
    print("user_iddddddddddd",user_id)
    if userDocument.exists:
        username = userDocument.to_dict().get("Username")
        allPosts = await getFeedForUser(user_id)  
        return templates.TemplateResponse("main.html", {
            "request": request,
            "user_token": user_token,
            "UserName": username,
            "AllPosts": allPosts
        })

    allUsernames = getAllUsernames()

    return templates.TemplateResponse("userName.html", {
        "request": request,
        "user_token": user_token,
        "user_names": allUsernames
    })



@app.post("/submitUsername", response_class=HTMLResponse)
async def submitUsername(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        return RedirectResponse("/", status_code=302)

    form = await request.form()
    enteredName = form["username"].strip()

    if not enteredName:
        return templates.TemplateResponse("userName.html", {
            "request": request,
            "user_token": user_token,
            "error_message": "Username cannot be empty",
            "user_names": getAllUsernames()  
        })

    existingUsers = firestore_db.collection("User").where("Username", "==", enteredName).stream()
    if any(existingUsers):
        return templates.TemplateResponse("userName.html", {
            "request": request,
            "user_token": user_token,
            "error_message": f"Username '{enteredName}' is already taken.",
            "user_names": getAllUsernames()
        })

    userId = user_token["user_id"]
    firestore_db.collection("User").document(userId).set({
        "Username": enteredName,
        "Followers": [],
        "Following": [],
        "CreatedAt": datetime.utcnow().isoformat()
    })

    return RedirectResponse("/", status_code=302)


@app.get("/profile/{username}", response_class=HTMLResponse)
async def profilePage(request: Request, username: str):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    viewerUserId = user_token["user_id"] if user_token else None
    viewerUserDoc = firestore_db.collection("User").document(viewerUserId).get() if viewerUserId else None
    viewerUsername = viewerUserDoc.to_dict().get("Username") if viewerUserDoc and viewerUserDoc.exists else None

    userQuery = firestore_db.collection("User").where("Username", "==", username).stream()
    profileDoc = next(userQuery, None)

    if not profileDoc:
        return HTMLResponse("User not found", status_code=404)

    profileData = profileDoc.to_dict()
    profileFollowers = profileData.get("Followers", [])
    profileFollowing = profileData.get("Following", [])

    isMyProfile = (viewerUsername == username)
    isFollowing = (viewerUsername in profileFollowers) if viewerUsername else False

    posts_query = firestore_db.collection("Post")\
        .where("Username", "==", username)\
        .order_by("Date", direction=firestore.Query.DESCENDING)\
        .stream()

    posts = [post.to_dict() for post in posts_query]

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "username": username,
        "posts": posts,
        "followers_count": len(profileFollowers),
        "following_count": len(profileFollowing),
        "is_my_profile": isMyProfile,
        "is_following": isFollowing  
    })



@app.post("/toggle-follow/{username}", response_class=RedirectResponse)
async def toggle_follow(request: Request, username: str):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    viewerUserId = user_token.get("user_id")
    viewerUserRef = firestore_db.collection("User").document(viewerUserId)
    viewerUserDoc = viewerUserRef.get()

    if not viewerUserDoc.exists:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    viewerData = viewerUserDoc.to_dict()
    viewerUsername = viewerData.get("Username")

    targetUserQuery = firestore_db.collection("User").where("Username", "==", username).stream()
    targetUserDoc = next(targetUserQuery, None)

    if not targetUserDoc:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    targetUserId = targetUserDoc.id
    targetUserRef = firestore_db.collection("User").document(targetUserId)
    targetUserData = targetUserDoc.to_dict()

    viewerFollowing = viewerData.get("Following", [])
    targetFollowers = targetUserData.get("Followers", [])

    if username in viewerFollowing:
        viewerFollowing.remove(username)
        targetFollowers.remove(viewerUsername)
    else:
        viewerFollowing.append(username)
        targetFollowers.append(viewerUsername)

    viewerUserRef.update({"Following": viewerFollowing})
    targetUserRef.update({"Followers": targetFollowers})

    return RedirectResponse(f"/profile/{username}", status_code=status.HTTP_302_FOUND)



@app.get("/followers/{username}", response_class=HTMLResponse)
async def showFollowers(request: Request, username: str):
    userQuery = firestore_db.collection("User").where("Username", "==", username).stream()
    userDoc = next(userQuery, None)

    if not userDoc:
        return HTMLResponse("User not found", status_code=404)

    userData = userDoc.to_dict()
    followers = list(reversed(userData.get("Followers", [])))

    return templates.TemplateResponse("usersList.html", {
        "request": request,
        "title": f"{username}'s Followers",
        "users": followers
    })

@app.get("/following/{username}", response_class=HTMLResponse)
async def showFollowing(request: Request, username: str):
    userQuery = firestore_db.collection("User").where("Username", "==", username).stream()
    userDoc = next(userQuery, None)

    if not userDoc:
        return HTMLResponse("User not found", status_code=404)

    userData = userDoc.to_dict()
    following = list(reversed(userData.get("Following", [])))

    return templates.TemplateResponse("usersList.html", {
        "request": request,
        "title": f"{username} is Following",
        "users": following
    })

@app.get("/search", response_class=HTMLResponse)
async def searchProfiles(request: Request, query: str = ""):
    if not query:
        return RedirectResponse("/", status_code=302)

    usersRef = firestore_db.collection("User").stream()

    matchingUsers = []
    for user in usersRef:
        data = user.to_dict()
        username = data.get("Username", "")
        if username.lower().startswith(query.lower()):
            matchingUsers.append(username)

    return templates.TemplateResponse("search_results.html", {
        "request": request,
        "query": query,
        "users": matchingUsers
    })



@app.post("/create-post", response_class=RedirectResponse)
async def createPost(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    form = await request.form()

    imageFile = form.get("image")
    caption = form.get("caption")
    matchingUrl = None

    if not user_token:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    userId = user_token.get("user_id")
    userCollection = firestore_db.collection("User").document(userId)
    userDoc = userCollection.get()

    if not userDoc.exists:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    username = userDoc.to_dict().get("Username")

    if not imageFile or not imageFile.filename:
        return HTMLResponse(content="""
            <h3 style="color: red; text-align: center; margin-top: 50px;">
             Please upload a valid image file (PNG or JPG).<br>
            <a href="/profile/{}" style="color: blue;">Go Back</a>
            </h3>
        """.format(username), status_code=400)

    allowedTypes = ["image/png", "image/jpeg"]
    if imageFile.content_type not in allowedTypes:
        return HTMLResponse(content="""
            <h3 style="color: red; text-align: center; margin-top: 50px;">
             Only PNG or JPG images are allowed.<br>
            <a href="/profile/{}" style="color: blue;">Go Back</a>
            </h3>
        """.format(username), status_code=400)

    try:
        storage_client = storage.Client(project=local_constants.PROJECT_NAME)
        bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)

        addFile(imageFile)
        filename = imageFile.filename

        blobs = blobList(None)
        imageUrls = [
            f"https://storage.googleapis.com/{local_constants.PROJECT_STORAGE_BUCKET}/{blob.name}"
            for blob in blobs
        ]

        for path in imageUrls:
            if filename in path:
                matchingUrl = path
                break

    except Exception as e:
        print("Error uploadingggggg", str(e))
        return HTMLResponse(content="""
            <h3 style="color: red; text-align: center; margin-top: 50px;">
             Failed to upload image. Try again.<br>
            <a href="/profile/{}" style="color: blue;">Go Back</a>
            </h3>
        """.format(username), status_code=400)

    postCollection = firestore_db.collection("Post").document()
    postData = {
        "Username": username,
        "Caption": caption,
        "ImageURL": matchingUrl,
        "Date": datetime.utcnow().isoformat(),
        "PostId": postCollection.id
    }
    postCollection.set(postData)

    userPosts = userDoc.to_dict().get("posts", [])
    userPosts.append({
        "Caption": caption,
        "ImageURL": matchingUrl,
        "PostId": postCollection.id
    })
    userCollection.update({"posts": userPosts})

    return RedirectResponse(f"/profile/{username}", status_code=status.HTTP_302_FOUND)


@app.post("/add-comment/{post_id}", response_class=RedirectResponse)
async def addComment(request: Request, post_id: str, comment_text: str = Form(...)):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    if len(comment_text) > 200:
        return HTMLResponse(content="""
        <h3 style="color: red; text-align: center; margin-top: 50px;">
         Comment must be under 200 characters.<br>
        <a href="/" style="color: blue;">Go Back</a>
        </h3>
        """, status_code=400)

    userId = user_token.get("user_id")
    userDoc = firestore_db.collection("User").document(userId).get()

    if not userDoc.exists:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    username = userDoc.to_dict().get("Username")

    postRef = firestore_db.collection("Post").document(post_id)
    postDoc = postRef.get()

    if not postDoc.exists:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    postData = postDoc.to_dict()
    comments = postData.get("Comments", [])

    comments.append({
        "username": username,
        "text": comment_text
    })

    postRef.update({"Comments": comments})

    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    

def addDirectory(directory_name):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)

    blob = bucket.blob(directory_name)
    blob.upload_from_string('',content_type="application/x-www-form-urlencoded;charset=UTF-8")

def addFile(file):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)


    print(file.filename,bucket,"checkkkkkkkkkkk")
    blob = storage.Blob(file.filename,bucket)
    blob.upload_from_file(file.file)

def blobList(prefix):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)

    return storage_client.list_blobs(local_constants.PROJECT_STORAGE_BUCKET,prefix=prefix)






