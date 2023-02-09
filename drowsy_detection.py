import os
import time

import cv2
import dlib
import mediapipe as mp
import numpy as np
import pymongo
import pyrebase
import streamlit as st
from bson.objectid import ObjectId
from imutils import face_utils
from mediapipe.python.solutions.drawing_utils import \
    _normalized_to_pixel_coordinates as denormalize_coordinates
from scipy.spatial import distance as dist

config = {"apiKey": "AIzaSyCNPBcskQFs2tn5UfdFbP8LzbnEMIarsWc",
  "authDomain": "aps-csia.firebaseapp.com",
  "databaseURL": "https://aps-csia-default-rtdb.asia-southeast1.firebasedatabase.app/",
  "projectId": "aps-csia",
  "storageBucket": "aps-csia.appspot.com",
  "messagingSenderId": "1069559357849",
  "appId": "1:1069559357849:web:39e9d0139d42a206973308",
  "measurementId": "G-FVTG7XGLN7"}

firebase = pyrebase.initialize_app(config)
db = firebase.database()

print("-> Loading the predictor and detector...")
detector = cv2.CascadeClassifier("./haarcascade_frontalface_default.xml")    #Faster but less accurate
predictor = dlib.shape_predictor('./shape_predictor_68_face_landmarks.dat')

def get_mediapipe_app(
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
):
    """Initialize and return Mediapipe FaceMesh Solution Graph object"""
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=max_num_faces,
        refine_landmarks=refine_landmarks,
        min_detection_confidence=min_detection_confidence,
        min_tracking_confidence=min_tracking_confidence,
    )

    return face_mesh

def distance(point_1, point_2):
    """Calculate l2-norm between two points"""
    dist = sum([(i - j) ** 2 for i, j in zip(point_1, point_2)]) ** 0.5
    return dist


def get_ear(landmarks, refer_idxs, frame_width, frame_height):
    """
    Calculate Eye Aspect Ratio for one eye.

    Args:
        landmarks: (list) Detected landmarks list
        refer_idxs: (list) Index positions of the chosen landmarks
                            in order P1, P2, P3, P4, P5, P6
        frame_width: (int) Width of captured frame
        frame_height: (int) Height of captured frame

    Returns:
        ear: (float) Eye aspect ratio
    """
    try:
        # Compute the euclidean distance between the horizontal
        coords_points = []
        for i in refer_idxs:
            lm = landmarks[i]
            coord = denormalize_coordinates(lm.x, lm.y, frame_width, frame_height)
            coords_points.append(coord)

        # Eye landmark (x, y)-coordinates
        P1_P4 = dist.euclidean(coords_points[0], coords_points[3])
        P2_P6 = dist.euclidean(coords_points[1], coords_points[5])
        P3_P5 = dist.euclidean(coords_points[2], coords_points[4])

        # Compute the eye aspect ratio
        ear = (P2_P6 + P3_P5) / (2.0 * P1_P4)

    except:
        ear = 0.0
        coords_points = None

    return ear, coords_points


def calculate_avg_ear(landmarks, left_eye_idxs, right_eye_idxs, image_w, image_h):
    # Calculate Eye aspect ratio

    left_ear, left_lm_coordinates = get_ear(landmarks, left_eye_idxs, image_w, image_h)
    right_ear, right_lm_coordinates = get_ear(landmarks, right_eye_idxs, image_w, image_h)
    Avg_EAR = (left_ear + right_ear) / 2.0

    return Avg_EAR, (left_lm_coordinates, right_lm_coordinates)


def plot_eye_landmarks(frame, left_lm_coordinates, right_lm_coordinates, color):
    for lm_coordinates in [left_lm_coordinates, right_lm_coordinates]:
        if lm_coordinates:
            for coord in lm_coordinates:
                cv2.circle(frame, coord, 2, color, -1)

    return frame

def lip_distance(shape):
    top_lip = shape[50:53]
    top_lip = np.concatenate((top_lip, shape[61:64]))

    low_lip = shape[56:59]
    low_lip = np.concatenate((low_lip, shape[65:68]))

    top_mean = np.mean(top_lip, axis=0)
    low_mean = np.mean(low_lip, axis=0)

    distance = abs(top_mean[1] - low_mean[1])
    return distance


def plot_text(image, text, origin, color, font=cv2.FONT_HERSHEY_SIMPLEX, fntScale=0.8, thickness=2):
    image = cv2.putText(image, text, origin, font, fntScale, color, thickness)
    return image

class VideoFrameHandler:
    def __init__(self):
        """
        Initialize the necessary constants, mediapipe app
        and tracker variables
        """
        # Left and right eye chosen landmarks.
        self.count_drowsy = 0
        self.count_yawn = 0
        self.eye_idxs = {
            "left": [362, 385, 387, 263, 373, 380],
            "right": [33, 160, 158, 133, 153, 144],
        }

        # Used for coloring landmark points.
        # Its value depends on the current EAR value.
        self.RED = (0, 0, 255)  # BGR
        self.GREEN = (0, 255, 0)  # BGR

        # Initializing Mediapipe FaceMesh solution pipeline
        self.facemesh_model = get_mediapipe_app()

        # For tracking counters and sharing states in and out of callbacks.
        self.state_tracker = {
            "start_time": time.perf_counter(),
            "DROWSY_TIME": 0.0,  # Holds the amount of time passed with EAR < EAR_THRESH
            "COLOR": self.GREEN,
            "play_alarm": False,
        }

        self.EAR_txt_pos = (10, 30)

    def process(self, frame: np.array, thresholds: dict):
        """
        This function is used to implement our Drowsy detection algorithm

        Args:
            frame: (np.array) Input frame matrix.
            thresholds: (dict) Contains the two threshold values
                               WAIT_TIME and EAR_THRESH.

        Returns:
            The processed frame and a boolean flag to
            indicate if the alarm should be played or not.
        """

        # To improve performance,
        # mark the frame as not writeable to pass by reference.
        frame = cv2.flip(frame,1)
        frame.flags.writeable = False
        frame_h, frame_w, _ = frame.shape

        DROWSY_TIME_txt_pos = (10, int(frame_h // 2 * 1.7))
        ALM_txt_pos = (10, int(frame_h // 2 * 1.85))

        #frame = cv2.flip(frame, 1)
        results = self.facemesh_model.process(frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        rects = detector.detectMultiScale(gray, scaleFactor=1.1, 
        minNeighbors=5, minSize=(30, 30),
        flags=cv2.CASCADE_SCALE_IMAGE)
        for (x, y, w, h) in rects:
            rect = dlib.rectangle(int(x), int(y), int(x + w),int(y + h))
                    
            shape = predictor(gray, rect)
            shape = face_utils.shape_to_np(shape) 
                    
            distance = lip_distance(shape) 
            lip = shape[48:60]
            cv2.drawContours(frame, [lip], -1, (0, 255, 0), 1)
            if (distance > thresholds["LIP_THRESH"]):
                plot_text(frame, "Yawn Alert", (460, 440), (0, 0, 255))
                time.sleep(1)
                self.count_yawn += 1
                if os.environ.get("logged_in") == "True":
                    user_id = os.environ.get("user_id")
                    email = os.environ.get("email")
                    client = pymongo.MongoClient("mongodb+srv://admin:Admin123@aps.agcjjww.mongodb.net/?retryWrites=true&w=majority")
                    db = client["aps-db"]
                    users = db["count"]
                    user = users.find_one({"_id": ObjectId(user_id)})
                    if user:
                        users.update_one({"_id": ObjectId(user_id)}, {"$set": {"count_yawn": self.count_yawn, "email": email}})
                    else:
                        users.insert_one({"_id": ObjectId(user_id), "count_yawn": self.count_yawn, "email": email})
                else:
                    st.error("Not logged in")
        frame = plot_text(frame, f"Yawn count: {self.count_yawn}",(420,410), color=(0, 255, 0), thickness=2)

        if results.multi_face_landmarks:
            landmarks = results.multi_face_landmarks[0].landmark
            EAR, coordinates = calculate_avg_ear(landmarks, self.eye_idxs["left"], self.eye_idxs["right"], frame_w, frame_h)
            frame = plot_eye_landmarks(frame, coordinates[0], coordinates[1], self.state_tracker["COLOR"])
            
            if EAR < thresholds["EAR_THRESH"]:

                # Increase DROWSY_TIME to track the time period with EAR less than the threshold
                # and reset the start_time for the next iteration.
                end_time = time.perf_counter()

                self.state_tracker["DROWSY_TIME"] += end_time - self.state_tracker["start_time"]
                self.state_tracker["start_time"] = end_time
                self.state_tracker["COLOR"] = self.RED              

                if self.state_tracker["DROWSY_TIME"] >= thresholds["WAIT_TIME"]:
                    self.state_tracker["play_alarm"] = True
                    plot_text(frame, "WAKE UP! WAKE UP", ALM_txt_pos, self.state_tracker["COLOR"])
                    time.sleep(1)
                    self.count_drowsy += 1
                    if os.environ.get("logged_in") == "True":
                        user_id = os.environ.get("user_id")
                        email = os.environ.get("email")
                        client = pymongo.MongoClient("mongodb+srv://admin:Admin123@aps.agcjjww.mongodb.net/?retryWrites=true&w=majority")
                        db = client["aps-db"]
                        users = db["count"]
                        user = users.find_one({"_id": ObjectId(user_id)})
                        if user:
                            users.update_one({"_id": ObjectId(user_id)}, {"$set": {"count_drowsy": self.count_drowsy, "email": email}})
                        else:
                            users.insert_one({"_id": ObjectId(user_id), "count_drowsy": self.count_drowsy, "email": email})
            else:
                self.state_tracker["start_time"] = time.perf_counter()
                self.state_tracker["DROWSY_TIME"] = 0.0
                self.state_tracker["COLOR"] = self.GREEN
                self.state_tracker["play_alarm"] = False

            EAR_txt = f"EAR: {round(EAR, 2)}"
            DROWSY_TIME_txt = f"DROWSY: {round(self.state_tracker['DROWSY_TIME'], 3)} Secs"
            plot_text(frame, EAR_txt, self.EAR_txt_pos, self.state_tracker["COLOR"])
            plot_text(frame, DROWSY_TIME_txt, DROWSY_TIME_txt_pos, self.state_tracker["COLOR"])
            frame = plot_text(frame, f"Drowsy count: {self.count_drowsy}",(400,30), color=(0, 255, 0), thickness=2)

        else:
            self.state_tracker["start_time"] = time.perf_counter()
            self.state_tracker["DROWSY_TIME"] = 0.0
            self.state_tracker["COLOR"] = self.GREEN
            self.state_tracker["play_alarm"] = False
        
        return frame, self.state_tracker["play_alarm"]