import os
import threading

import av
import bcrypt
import pymongo
import streamlit as st
from streamlit_option_menu import option_menu
from streamlit_webrtc import VideoHTMLAttributes, webrtc_streamer
from twilio.rest import Client

from audio_handling import AudioFrameHandler
from drowsy_detection import VideoFrameHandler

# Define the audio file to use.
alarm_file_path = os.path.join("audio", "wake_up.wav")
logged_in = False

def update_sliders():
    slider_wait = 1.0
    eye_thresh = 0.18
    lip_thresh = 0.2

    client = pymongo.MongoClient("mongodb+srv://admin:Admin123@aps.agcjjww.mongodb.net/?retryWrites=true&w=majority")
    db = client["aps-db"]
    slider_values = db["slider-values"]

    if slider_wait != WAIT_TIME:
        slider_values.update_one({"slider_name": "Wait_Time"}, {"$set": {"value": WAIT_TIME}}, upsert=True)
    if eye_thresh != EAR_THRESH:
        slider_values.update_one({"slider_name": "Eye_Threshold"}, {"$set": {"value": EAR_THRESH}}, upsert=True)
    if lip_thresh != LIP_THRESH:
        slider_values.update_one({"slider_name": "Lip_Threshold"}, {"$set": {"value": LIP_THRESH}}, upsert=True)

# Streamlit Components
st.set_page_config(
    page_title="Drowsiness Detection | APS",
    page_icon="https://framerusercontent.com/modules/466qV2P53XLpEfUjjmZC/FNnZTYISEsUGPpjII54W/assets/VebAVoINVBFxBTpsrQVLHznVo.png",
    initial_sidebar_state="expanded",
    layout="wide",
)

menu_choice = option_menu(
            menu_title=None,  
            options=["Home", "Login", "Signup", "OTP Login", "About"],  
            icons=["house", "box-arrow-in-right", "pencil-square","telephone", "question-circle"],  
            menu_icon="cast",  
            default_index=0,  
            orientation="horizontal",
        )

if menu_choice == "Home":
    st.title("Drowsiness Detection")
    with st.container():
        c1, c2, c3 = st.columns(spec=[1, 1, 1])
        with c1:
            # The amount of time (in seconds) to wait before sounding the alarm.
            WAIT_TIME = st.slider("Time to wait before sounding alarm:", 0.0, 5.0, 1.0, 0.25)

        with c2:
            # Lowest valid value of Eye Aspect Ratio. Ideal values [0.15, 0.2].
            EAR_THRESH = st.slider("Eye Aspect Ratio threshold:", 0.0, 0.4, 0.18, 0.01)
        
        with c3:
            # Lip threshold to detect yawning
            LIP_THRESH = st.slider("Lip threshold:", 0.0, 0.4, 0.2, 0.01)
            LIP_THRESH = LIP_THRESH*100

    thresholds = {
        "EAR_THRESH": EAR_THRESH,
        "WAIT_TIME": WAIT_TIME, 
        "LIP_THRESH": LIP_THRESH
    }
    update_sliders()

    # For streamlit-webrtc
    video_handler = VideoFrameHandler()
    audio_handler = AudioFrameHandler(sound_file_path=alarm_file_path)

    lock = threading.Lock()  # For thread-safe access & to prevent race-condition.
    shared_state = {"play_alarm": False}  # Shared state between callbacks.

    def video_frame_callback(frame: av.VideoFrame):
        frame = frame.to_ndarray(format="bgr24")  # Decode and convert frame to RGB

        frame, play_alarm = video_handler.process(frame, thresholds)  # Process frame
        with lock:
            shared_state["play_alarm"] = play_alarm  # Update shared state

        return av.VideoFrame.from_ndarray(frame, format="bgr24")  # Encode and return BGR frame

    def audio_frame_callback(frame: av.AudioFrame):
        with lock:  # access the current “play_alarm” state
            play_alarm = shared_state["play_alarm"]

        new_frame: av.AudioFrame = audio_handler.process(frame, play_sound=play_alarm)
        return new_frame

    ctx = webrtc_streamer(
        key="drowsiness-detection",
        video_frame_callback=video_frame_callback,
        audio_frame_callback=audio_frame_callback,
        rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},  # Add this to config for cloud deployment.
        media_stream_constraints={"video": {"height": {"ideal": 480}}, "audio": True},
        video_html_attrs=VideoHTMLAttributes(autoPlay=True, controls=False, muted=False),
    )

#handling login

if menu_choice == "Login":
    st.text('Login')
    email = st.text_input('Enter email')
    password = st.text_input('Enter password', type="password")
    if st.button('Login'):
        try:
            client = pymongo.MongoClient("mongodb+srv://admin:Admin123@aps.agcjjww.mongodb.net/?retryWrites=true&w=majority")
            db = client["aps-db"]
            users = db["users"]
            user = users.find_one({"email": email})
            if user:
                if bcrypt.checkpw(password.encode(), user["password"]):
                    st.success("Successfully logged in!")
                    menu_choice = "Home"
                    os.environ["email"] = email
                    os.environ["user_id"] = str(user["_id"])
                    os.environ["logged_in"] = "True"
                else:
                    st.error("Invalid password")
            else:
                st.error("Email not found")
        except Exception as e:
                st.error("An error occurred: {}".format(str(e)))

if menu_choice == "Signup":
    st.text('Signup')
    email = st.text_input('Enter email')
    password = st.text_input('Enter password', type="password")
    if st.button('Signup'):
        try:
            client = pymongo.MongoClient("mongodb+srv://admin:Admin123@aps.agcjjww.mongodb.net/?retryWrites=true&w=majority")
            db = client["aps-db"]
            users = db["users"]
            if users.find_one({"email": email}):
                st.error("Email already exists")
            else:
                hashed_password = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
                users.insert_one({"email": email, "password": hashed_password})
                st.success("Successfully signed up!")
        except Exception as e:
            st.error("An error occurred: {}".format(str(e)))
        
#phone number login
account_sid = "AC97fd9a03c4637fe246adcecc613bb153"
auth_token = os.environ["TWILIO_AUTH_TOKEN"]
verify_sid = "VA629a29a82eedcde1e6c89e5f586fdbfd"

if menu_choice == "OTP Login":
    st.text("Please enter country code followed by phone number")
    st.text("For example: +919255520023")
    verified_number = st.text_input("Enter your phone number")

    client = Client(account_sid, auth_token)
    otp_sent = False
    if st.button("Send OTP"):
        verification = client.verify.v2.services(verify_sid) \
        .verifications \
        .create(to=verified_number, channel="sms")
        if (verification.status == "pending" or verification.status == "started"):
            st.success("OTP sent successfully to " + verified_number)
            otp_sent = True
        else:
            st.error("Error sending OTP")

    otp_code = st.text_input("Please enter the OTP:",type="password")
    if st.button("Verify OTP"):
        verification_check = client.verify.v2.services(verify_sid) \
        .verification_checks \
        .create(to=verified_number, code=otp_code)
        if (verification_check.status == "approved"):
            st.success("OTP Verified")
            client = pymongo.MongoClient("mongodb+srv://admin:Admin123@aps.agcjjww.mongodb.net/?retryWrites=true&w=majority")
            db = client["aps-db"]
            users = db["users"]
            users.insert_one({"phone": verified_number})
        else:
            st.error("OTP Verification Failed")
            
    
hide_streamlit_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            </style>
            """
st.markdown(hide_streamlit_style, unsafe_allow_html=True)